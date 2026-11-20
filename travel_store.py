"""Account-scoped travel data. Every balance transition locks the account first."""
import json
import uuid
from web_auth import is_test_admin
from fastapi import HTTPException


def account(c, uid):
    c.execute("INSERT INTO travel_accounts(owner_id) VALUES (%s) ON DUPLICATE KEY UPDATE owner_id=owner_id",(uid,))
    c.execute("SELECT available,reserved,used,profile FROM travel_accounts WHERE owner_id=%s FOR UPDATE",(uid,))
    return c.fetchone()


def credits(row):
    return {key: row[key] for key in ("available","reserved","used")}


def account_data(store, uid, action, values=None):
    with store.cursor() as c:
        row=account(c,uid)
        if action=="credits":
            return {**credits(row), "unlimited": True} if is_test_admin(uid) else credits(row)
        profile=json.loads(row["profile"] or "{}")
        if values is not None:
            profile.update(values)
            c.execute("UPDATE travel_accounts SET profile=%s WHERE owner_id=%s",(json.dumps(profile),uid))
        return profile


def order(store,uid,sku=None,order_id=None,outcome=None):
    with store.cursor() as c:
        row=account(c,uid)
        if order_id is None:
            if sku!="travel-one":
                raise HTTPException(422,"未知模拟商品")
            order_id=uuid.uuid4().hex
            c.execute("INSERT INTO travel_orders VALUES (%s,%s,%s,100,1,'pending')",(order_id,uid,sku))
        c.execute("SELECT id,sku,amount_fen,credits,status FROM travel_orders WHERE id=%s AND owner_id=%s FOR UPDATE",(order_id,uid))
        data=c.fetchone()
        if not data:
            raise HTTPException(404,"订单不存在")
        if outcome and data["status"]=="pending":
            c.execute("UPDATE travel_orders SET status=%s WHERE id=%s",(outcome,order_id))
            data["status"]=outcome
            if outcome=="success":
                c.execute("UPDATE travel_accounts SET available=available+%s WHERE owner_id=%s",(data["credits"],uid))
                row["available"]+=data["credits"]
        elif outcome and data["status"]!=outcome:
            raise HTTPException(409,"订单已结束")
        return {**data,"order_id":order_id,"order":data,"balance":credits(row)}


def reserve(store,uid,state,key,fingerprint,payload,saturated,daily_limit):
    with store.cursor() as c:
        row=account(c,uid)
        c.execute("SELECT run_id,fingerprint FROM travel_requests WHERE owner_id=%s AND request_key=%s",(uid,key))
        old=c.fetchone()
        if old:
            if old["fingerprint"]!=fingerprint:
                raise HTTPException(409,{"code":"idempotency_conflict","message":"请求内容已变更"})
            return old["run_id"]
        unlimited = is_test_admin(uid)
        if saturated and not unlimited:
            raise HTTPException(429,"运行中的任务已达上限")
        c.execute("SELECT COUNT(*) AS n FROM osint_runs WHERE owner_id=%s AND created_at >= DATE(UTC_TIMESTAMP()+INTERVAL 8 HOUR)-INTERVAL 8 HOUR",(uid,))
        if c.fetchone()["n"]>=daily_limit and not unlimited:
            raise HTTPException(429,"今日任务次数已达上限")
        if not row["available"] and not unlimited:
            raise HTTPException(409,{"code":"insufficient_credit","message":"旅行额度不足"})
        for item in payload["attachment_ids"]:
            c.execute("SELECT id FROM travel_attachments WHERE owner_id=%s AND id=%s",(uid,item))
            if not c.fetchone():
                raise HTTPException(404,"附件不存在")
        store.insert_run(c,uid,state)
        c.execute("INSERT INTO travel_requests VALUES (%s,%s,%s,%s,%s,%s)",
                  (state["id"],uid,key,fingerprint,json.dumps(payload), 'exempt' if unlimited else 'reserved'))
        if unlimited:
            return state['id']
        c.execute("UPDATE travel_accounts SET available=available-1,reserved=reserved+1 WHERE owner_id=%s",(uid,))
        return state["id"]


def transition(store,uid,rid,target):
    with store.cursor() as c:
        row=account(c,uid)
        c.execute("SELECT credit_state FROM travel_requests WHERE run_id=%s AND owner_id=%s FOR UPDATE",(rid,uid))
        req=c.fetchone()
        if not req or req["credit_state"] in (target, "exempt"):
            return
        change = {("released","reserved"):(-1,1,0),("reserved","released"):(1,-1,0),
                  ("reserved","used"):(0,-1,1)}.get((req["credit_state"],target))
        if not change:
            raise HTTPException(409,"额度状态不允许操作")
        if target=="reserved" and not row["available"]:
            raise HTTPException(409,{"code":"insufficient_credit","message":"旅行额度不足"})
        c.execute("UPDATE travel_accounts SET available=available+%s,reserved=reserved+%s,used=used+%s WHERE owner_id=%s",(*change,uid))
        c.execute("UPDATE travel_requests SET credit_state=%s WHERE run_id=%s",(target,rid))
        if target=="used":
            c.execute("UPDATE osint_runs SET status='done' WHERE id=%s",(rid,))


def load_request(store,uid,rid):
    with store.cursor() as c:
        c.execute("SELECT payload FROM travel_requests WHERE owner_id=%s AND run_id=%s",(uid,rid))
        row=c.fetchone()
        return json.loads(row["payload"]) if row else None

def reconcile(store):
    with store.cursor() as c:
        c.execute("""SELECT t.owner_id,t.run_id,r.status,
                     EXISTS(SELECT 1 FROM osint_run_artifacts a WHERE a.run_id=t.run_id
                     AND a.category='reports' AND OCTET_LENGTH(a.content)>0) AS readable
                     FROM travel_requests t JOIN osint_runs r ON r.id=t.run_id
                     WHERE t.credit_state='reserved'""")
        rows=c.fetchall()
    for row in rows:
        transition(store,row["owner_id"],row["run_id"],"used" if row["status"]=="done" and row["readable"] else "released")
