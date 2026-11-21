"""Account and session routes for MySQL-backed web deployments."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pymysql
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr
from travel_profile import VisitorProfile

SESSION_HOURS = 12
MAX_AGE = SESSION_HOURS * 3600
USER_PATTERN = re.compile(r"[a-zA-Z0-9_]{3,32}\Z")
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_-]{43}\Z")
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
router = APIRouter(prefix="/api/auth")


class OnlineSettings:
    def __init__(self):
        self.origin = os.getenv("OSINT_PUBLIC_ORIGIN", "").strip().rstrip("/")
        self.secure = os.getenv("OSINT_SECURE_COOKIE", "1") != "0"
        self.registration_enabled = os.getenv("OSINT_REGISTRATION_ENABLED", "1") == "1"
        self.signup_code = os.getenv("OSINT_SIGNUP_CODE", "").strip()
        self.max_daily_runs = int(os.getenv("OSINT_MAX_DAILY_RUNS", "8"))
        self.max_daily_questions = int(os.getenv("OSINT_MAX_DAILY_QUESTIONS", "30"))
        parsed = urlsplit(self.origin)
        if not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("请设置 OSINT_PUBLIC_ORIGIN 为完整网站源，例如 https://demo.example.com。")
        if self.secure and parsed.scheme != "https":
            raise ValueError("线上模式必须使用 HTTPS。")
        if not self.secure and (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("非 Secure Cookie 只用于本机 HTTP 开发。")
        if self.max_daily_runs < 1 or self.max_daily_questions < 1:
            raise ValueError("每日任务和问答上限必须是正整数。")
        self.cookie = "__Host-osint_session" if self.secure else "osint_session"


class Credentials(BaseModel):
    username: str
    password: SecretStr
    signup_code: SecretStr | None = None


class Registration(Credentials):
    profile: VisitorProfile | None = None  # Legacy research clients omit tourism fields.


def online_enabled():
    mode = os.getenv("OSINT_STORAGE", "files").strip()
    if mode not in {"mysql", "files"}:
        raise ValueError("OSINT_STORAGE 仅支持 mysql 或 files。")
    return mode == "mysql"


def store_for(request):
    app = request.scope.get("app") if request is not None else None
    return getattr(app.state, "online_store", None) if app is not None else None


def session_digest(request):
    value = request.cookies.get(request.app.state.online_settings.cookie, "")
    return hashlib.sha256(value.encode("ascii")).digest() if TOKEN_PATTERN.fullmatch(value) else None


def auth_ip(request):
    return request.client.host if request.client else "unknown"


def new_session():
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=SESSION_HOURS)
    return token, hashlib.sha256(token.encode("ascii")).digest(), expiry


def set_session(request, response, token):
    settings = request.app.state.online_settings
    response.set_cookie(
        settings.cookie, token, max_age=MAX_AGE, path="/", secure=settings.secure,
        httponly=True, samesite="lax",
    )


def validate_username(value):
    if not USER_PATTERN.fullmatch(value):
        raise HTTPException(422, "用户名需要 3–32 个英文字母、数字或下划线。")
    return value.lower()


def is_test_admin(user_id):
    """Only the operator-provisioned local account can bypass per-user quotas."""
    return (type(user_id) is int and str(user_id) == os.getenv("OSINT_TEST_ADMIN_ID")
            and os.getenv("OSINT_PUBLIC_ORIGIN", "").startswith("http://127.0.0.1:"))


async def use_limit(store, scope, identity, seconds, limit, detail):
    if scope in {'run', 'chat'} and is_test_admin(identity):
        return
    if not await asyncio.to_thread(store.rate_limit, scope, identity, seconds, limit):
        raise HTTPException(429, detail)


def verify_password(stored, supplied):
    try:
        return PASSWORDS.verify(stored, supplied)
    except (VerificationError, InvalidHashError, ValueError):
        return False


@router.get("/options")
async def options(request: Request):
    if not store_for(request):
        raise HTTPException(404)
    settings = request.app.state.online_settings
    return {"registration_enabled": settings.registration_enabled, "signup_code_required": bool(settings.signup_code)}


class TravelRegistration(Credentials):
    profile: VisitorProfile


@router.post("/travel-register", status_code=201)
async def register_traveler(form: TravelRegistration, request: Request, response: Response):
    return await register(Registration(**form.model_dump()), request, response)


@router.post("/register", status_code=201)
async def register(form: Registration, request: Request, response: Response):
    store = store_for(request)
    if not store:
        raise HTTPException(404)
    settings = request.app.state.online_settings
    if not settings.registration_enabled:
        raise HTTPException(403, "此实例暂未开放注册。")
    await use_limit(store, "register", auth_ip(request), 900, 8, "注册请求过多，请稍后再试。")
    username = validate_username(form.username)
    password = form.password.get_secret_value()
    if not 12 <= len(password) <= 128 or password.isspace():
        raise HTTPException(422, "密码需要 12–128 个字符。")
    if settings.signup_code:
        submitted = form.signup_code.get_secret_value() if form.signup_code else ""
        if not secrets.compare_digest(submitted.encode("utf-8"), settings.signup_code.encode("utf-8")):
            raise HTTPException(403, "邀请码无效。")
    token, digest, expiry = new_session()
    password_hash = await asyncio.to_thread(PASSWORDS.hash, password)
    try:
        user = await asyncio.to_thread(store.create_user, username, password_hash, digest, expiry, session_digest(request),
                                       form.profile.model_dump(exclude_none=True) if form.profile else None)
    except pymysql.IntegrityError as exc:
        if exc.args[0] != 1062:
            raise
        raise HTTPException(409, "用户名已被使用，请换一个。") from None
    set_session(request, response, token)
    return {"username": user["username"]}


@router.post("/login")
async def login(form: Credentials, request: Request, response: Response):
    store = store_for(request)
    if not store:
        raise HTTPException(404)
    await use_limit(store, "login-ip", auth_ip(request), 300, 30, "登录尝试过多，请稍后再试。")
    username = validate_username(form.username)
    await use_limit(store, "login-user", username, 300, 10, "登录尝试过多，请稍后再试。")
    password = form.password.get_secret_value()
    if not 1 <= len(password) <= 128:
        raise HTTPException(401, "用户名或密码不正确。")
    user = await asyncio.to_thread(store.get_credentials, username)
    valid = await asyncio.to_thread(verify_password, user["password_hash"] if user else DUMMY_HASH, password)
    if not valid or user is None:
        raise HTTPException(401, "用户名或密码不正确。")
    token, digest, expiry = new_session()
    await asyncio.to_thread(store.issue_session, user["id"], digest, expiry, session_digest(request))
    set_session(request, response, token)
    return {"username": user["username"]}


@router.get("/me")
async def me(request: Request):
    if not store_for(request):
        raise HTTPException(404)
    return {"username": request.state.user["username"]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    store = store_for(request)
    if not store:
        raise HTTPException(404)
    digest = session_digest(request)
    if digest:
        await asyncio.to_thread(store.revoke_session, digest)
    settings = request.app.state.online_settings
    response.delete_cookie(settings.cookie, path="/", secure=settings.secure, httponly=True, samesite="lax")
    return {"ok": True}
