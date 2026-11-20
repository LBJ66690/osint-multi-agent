import io
import json
import os
from pathlib import Path
import time
import uuid
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image
import config
import server


@unittest.skipUnless(os.getenv("OSINT_TEST_MYSQL")=="1","requires disposable migrated MySQL")
class AccountAPI(unittest.TestCase):
    def test_travel_report_credit_and_attachment_isolation(self):
        origin="http://127.0.0.1:8765"
        with patch.dict(os.environ,OSINT_STORAGE="mysql",OSINT_PUBLIC_ORIGIN=origin,OSINT_SECURE_COOKIE="0",
                        OSINT_SIGNUP_CODE="",OSINT_REGISTRATION_ENABLED="1"),patch.object(config,"DASHSCOPE_API_KEY",""):
            with TestClient(server.app,base_url=origin,headers={"Origin":origin}) as first:
                second=TestClient(server.app,base_url=origin,headers={"Origin":origin})
                for api in (first,second):
                    result=api.post("/api/auth/register",json={"username":"t"+uuid.uuid4().hex[:14],"password":"Example-travel-1234"})
                    self.assertEqual(result.status_code,201,result.text)
                    self.assertEqual(api.get("/api/travel/credits").json()["available"],1)
                image=io.BytesIO()
                Image.new("RGB",(20,20)).save(image,format="PNG")
                response=first.post("/api/travel/attachments",files={"file":("ticket.png",image.getvalue(),"image/png")})
                self.assertEqual(response.status_code,200,response.text)
                aid=response.json()["attachment_id"]
                self.assertEqual(second.get("/api/travel/attachments/"+aid).status_code,404)
                self.assertEqual(first.get("/api/travel/attachments/"+aid).status_code,200)
                body=json.loads(Path("contracts/travel/v1/fixtures/create-run.json").read_text(encoding="utf-8"))
                body["attachment_ids"]=[aid]
                body["trip"]["poi_ids"]=["fanjingshan-east"]
                body["trip"]["ticket_fields"]={"arrival_station":"铜仁站"}
                key={"Idempotency-Key":uuid.uuid4().hex}
                self.assertEqual(second.post("/api/runs",json=body,headers=key).status_code,404)
                created=first.post("/api/runs",json=body,headers=key)
                self.assertEqual(created.status_code,200,created.text)
                rid=created.json()["run_id"]
                self.assertEqual(first.post("/api/runs",json=body,headers=key).json()["run_id"],rid)
                self.assertEqual(first.post("/api/runs",json={**body,"task":"different"},headers=key).status_code,409)
                for _ in range(150):
                    detail=first.get("/api/runs/"+rid).json()
                    if detail["status"] in server.FINAL:
                        break
                    time.sleep(1)
                self.assertEqual(detail["status"],"done",detail.get("summary"))
                self.assertEqual(first.get("/api/travel/credits").json(),{"available":0,"reserved":0,"used":1})
                self.assertEqual(first.get("/api/runs/"+rid+"/report.md").status_code,200)
                self.assertEqual(second.get("/api/runs/"+rid+"/report.md").status_code,404)
                self.assertEqual(first.delete("/api/travel/attachments/"+aid).status_code,204)
                self.assertEqual(first.get("/api/travel/attachments/"+aid).status_code,404)
                second.close()

    def test_admin_can_run_with_zero_credits_and_personal_limits(self):
        from datetime import datetime, timezone
        from mysql_store import MySQLStore, mysql_settings
        import travel_store
        store = MySQLStore(mysql_settings())
        name = "t" + uuid.uuid4().hex[:14]
        uid = store.create_user(name, "test-only", uuid.uuid4().bytes * 2,
                                datetime.now(timezone.utc))["id"]
        with store.cursor() as cursor:
            cursor.execute("UPDATE travel_accounts SET available=0 WHERE owner_id=%s", (uid,))
        with patch.dict(os.environ, OSINT_TEST_ADMIN_ID=str(uid), OSINT_PUBLIC_ORIGIN="http://127.0.0.1:8768"):
            for _ in range(2):
                rid = uuid.uuid4().hex
                now = datetime.now(timezone.utc).isoformat()
                state = dict(id=rid,task="fixture",model_id="fixture",status="pending",
                             execution_mode="auto",search_strategy="legacy",created_at=now,updated_at=now)
                self.assertEqual(travel_store.reserve(store,uid,state,rid,"f"*64,{"attachment_ids":[]},True,0),rid)
                travel_store.transition(store,uid,rid,"used")
            self.assertEqual(travel_store.account_data(store,uid,"credits"),
                             dict(available=0,reserved=0,used=0,unlimited=True))
