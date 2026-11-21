"""Travel account settings and one-use login challenges."""
import base64
import hashlib
import io
import secrets

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, SecretStr

import web_auth as auth

router = APIRouter(prefix="/api/auth")


def digest(value):
    return hashlib.sha256(value.encode()).digest()


def challenge(cursor, username):
    code = "".join(secrets.choice("23456789") for _ in range(5))
    image = Image.new("RGB", (166, 54), "#eef6e9")
    draw = ImageDraw.Draw(image)
    for _ in range(8):
        draw.line((secrets.randbelow(166), secrets.randbelow(54), secrets.randbelow(166), secrets.randbelow(54)), fill="#adc7ba", width=1)
    font = ImageFont.load_default(size=32)
    for i, digit in enumerate(code):
        draw.text((10 + i*29, 3+secrets.randbelow(9)), digit, font=font, fill="#194c42")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    cursor.execute("""UPDATE osint_login_checks SET challenge_digest=%s,
        challenge_until=UTC_TIMESTAMP()+INTERVAL 2 MINUTE WHERE username=%s""", (digest(code), username))
    return {"message":"请填写图片中的五位数字，再次登录。","captcha_required":True,
            "image":"data:image/png;base64,"+base64.b64encode(buf.getvalue()).decode()}


def login_attempt(store, username, password, answer, token, expiry, previous):
    if secrets.randbelow(32)==0:
        with store.cursor() as c:
            c.execute("DELETE FROM osint_login_checks WHERE expires_at<=UTC_TIMESTAMP() LIMIT 100")
        with store.cursor() as c:
            c.execute("DELETE FROM osint_sessions WHERE expires_at<=UTC_TIMESTAMP() LIMIT 100")
    with store.cursor() as c:
        c.execute("""INSERT INTO osint_login_checks(username,expires_at) VALUES (%s,UTC_TIMESTAMP()+INTERVAL 30 MINUTE)
                     ON DUPLICATE KEY UPDATE failures=IF(expires_at<=UTC_TIMESTAMP(),0,failures)""",(username,))
        c.execute("""SELECT failures,challenge_digest,challenge_until>UTC_TIMESTAMP() AS fresh
                     FROM osint_login_checks WHERE username=%s FOR UPDATE""",(username,))
        guard=c.fetchone()
        if guard["failures"]>=3 and not (guard["fresh"] and guard["challenge_digest"] and
                secrets.compare_digest(guard["challenge_digest"],digest(answer))):
            return challenge(c,username)
        c.execute("SELECT id,password_hash FROM osint_users WHERE username=%s FOR UPDATE",(username,))
        user=c.fetchone()
        valid = len(password)<=128 and auth.verify_password(user["password_hash"] if user else auth.DUMMY_HASH,password)
        if not valid or not user:
            c.execute("UPDATE osint_login_checks SET failures=failures+1, expires_at=UTC_TIMESTAMP()+INTERVAL 30 MINUTE WHERE username=%s",(username,))
            return challenge(c,username) if guard["failures"]>=2 else "用户名或密码不正确。"
        c.execute("DELETE FROM osint_login_checks WHERE username=%s",(username,))
        if previous:
            c.execute("DELETE FROM osint_sessions WHERE token_digest=%s",(previous,))
        c.execute("INSERT INTO osint_sessions(token_digest,user_id,expires_at) VALUES(%s,%s,%s)",(token,user["id"],expiry))


class Username(BaseModel):
    username: str


@router.post("/login-challenge")
def refresh_challenge(form: Username, request: Request):
    store=auth.store_for(request)
    if not store: raise HTTPException(404)
    username=auth.validate_username(form.username)
    if not store.rate_limit("captcha-refresh",auth.auth_ip(request),300,60): raise HTTPException(429,"请稍后再换一张。")
    with store.cursor() as c:
        c.execute("SELECT failures FROM osint_login_checks WHERE username=%s AND expires_at>UTC_TIMESTAMP() FOR UPDATE",(username,))
        row=c.fetchone()
        return challenge(c,username) if row and row["failures"]>=3 else {"captcha_required":False}


class Reset(Username):
    recovery_code: SecretStr
    new_password: SecretStr


class Confirm(BaseModel):
    password: SecretStr


class Change(Confirm):
    new_password: SecretStr


def signed_in(request):
    store=auth.store_for(request)
    if not store: raise HTTPException(404)
    return store,request.state.user["id"]


def verify_owner(c,uid,password):
    c.execute("SELECT password_hash FROM osint_users WHERE id=%s FOR UPDATE",(uid,))
    row=c.fetchone()
    if not row or not auth.verify_password(row["password_hash"],password.get_secret_value()):
        raise HTTPException(403,"当前密码不正确。")


def check_limit(store,uid):
    if not store.rate_limit("account-confirm",uid,300,12): raise HTTPException(429,"尝试过多，请稍后再试。")


def change_password(c,uid,password):
    value=password.get_secret_value()
    auth.validate_password(value)
    c.execute("UPDATE osint_users SET password_hash=%s WHERE id=%s",(auth.PASSWORDS.hash(value),uid))
    c.execute("DELETE FROM osint_sessions WHERE user_id=%s",(uid,))


@router.post("/reset-password")
def reset(form: Reset,request: Request):
    store=auth.store_for(request)
    if not store: raise HTTPException(404)
    name=auth.validate_username(form.username)
    if not store.rate_limit("recover-ip",auth.auth_ip(request),900,20) or not store.rate_limit("recover-user",name,900,8):
        raise HTTPException(429,"找回尝试过多，请稍后再试。")
    with store.cursor() as c:
        c.execute("SELECT id FROM osint_users WHERE username=%s FOR UPDATE",(name,))
        user=c.fetchone()
        c.execute("SELECT recovery_digest FROM travel_security WHERE owner_id=%s FOR UPDATE",(user["id"] if user else 0,))
        row=c.fetchone()
        if not row or not row["recovery_digest"] or not secrets.compare_digest(row["recovery_digest"],digest(form.recovery_code.get_secret_value().strip())):
            raise HTTPException(400,"用户名或恢复码无效。")
        change_password(c,user["id"],form.new_password)
        code=secrets.token_urlsafe(24)
        c.execute("UPDATE travel_security SET recovery_digest=%s WHERE owner_id=%s",(digest(code),user["id"]))
    return {"recovery_code":code}


@router.post("/password")
def update_password(form: Change,request: Request):
    store,uid=signed_in(request)
    check_limit(store,uid)
    with store.cursor() as c:
        verify_owner(c,uid,form.password)
        change_password(c,uid,form.new_password)
    return {"ok":True}


@router.post("/recovery-code")
def recovery_code(form: Confirm,request: Request):
    store,uid=signed_in(request)
    check_limit(store,uid)
    code=secrets.token_urlsafe(24)
    with store.cursor() as c:
        verify_owner(c,uid,form.password)
        c.execute("""INSERT INTO travel_security(owner_id,recovery_digest) VALUES(%s,%s)
                     ON DUPLICATE KEY UPDATE recovery_digest=%s""",(uid,digest(code),digest(code)))
    return {"recovery_code":code}


@router.post("/close-account")
def close_account(form: Confirm, request: Request):
    store,uid=signed_in(request)
    check_limit(store,uid)
    with store.cursor() as c:
        verify_owner(c,uid,form.password)
        c.execute("SELECT id FROM osint_runs WHERE owner_id=%s AND status NOT IN ('done','error','cancelled') LIMIT 1",(uid,))
        if c.fetchone(): raise HTTPException(409,"请先结束正在进行的行程。")
        c.execute("DELETE FROM osint_users WHERE id=%s",(uid,))
    return {"ok":True}


@router.get("/settings")
def settings(request:Request):
    store,uid=signed_in(request)
    with store.cursor() as c:
        c.execute("""SELECT u.created_at, s.avatar IS NOT NULL AS has_avatar,s.recovery_digest IS NOT NULL AS can_recover
                     FROM osint_users u LEFT JOIN travel_security s ON s.owner_id=u.id WHERE u.id=%s""",(uid,))
        return c.fetchone()


@router.get("/avatar")
def avatar(request:Request):
    store,uid=signed_in(request)
    with store.cursor() as c:
        c.execute("SELECT avatar FROM travel_security WHERE owner_id=%s",(uid,))
        row=c.fetchone()
    if not row or not row["avatar"]: raise HTTPException(404)
    return Response(row["avatar"],media_type="image/jpeg",headers={"Cache-Control":"private,no-store"})


@router.post("/avatar")
def upload_avatar(request:Request,file:UploadFile=File(...)):
    store,uid=signed_in(request)
    raw=file.file.read(4*1024*1024+1)
    if len(raw)>4*1024*1024: raise HTTPException(413,"头像请小于 4 MB。")
    out=io.BytesIO()
    try:
        with Image.open(io.BytesIO(raw),formats=("PNG","JPEG","WEBP")) as img:
            if img.width*img.height>12_000_000: raise ValueError()
            ImageOps.fit(ImageOps.exif_transpose(img).convert("RGB"),(256,256)).save(out,format="JPEG",quality=85)
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise HTTPException(422,"请选择有效的 JPG、PNG 或 WebP 图片。")
    with store.cursor() as c:
        c.execute("INSERT INTO travel_security(owner_id,avatar) VALUES(%s,%s) ON DUPLICATE KEY UPDATE avatar=%s",(uid,out.getvalue(),out.getvalue()))
    return {"ok":True}
