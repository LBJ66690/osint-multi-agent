"""Verify the account lifecycle on a dedicated migrated MySQL instance."""
import os
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
import server


@unittest.skipUnless(os.getenv("OSINT_TEST_MYSQL") == "1", "requires disposable MySQL")
class AccountSecurity(unittest.TestCase):
    def test_account_challenge_recovery_and_history(self):
        origin="http://127.0.0.1:8765"
        name="qa_"+uuid.uuid4().hex[:14]
        password="六字符通行证"
        with patch.dict(os.environ,OSINT_STORAGE="mysql",OSINT_PUBLIC_ORIGIN=origin,OSINT_SECURE_COOKIE="0",
                        OSINT_SIGNUP_CODE="",OSINT_REGISTRATION_ENABLED="1"), TestClient(server.app,base_url=origin,headers={"Origin":origin}) as api:
            self.assertEqual(api.post("/api/auth/register",json={"username":name,"password":"short"}).status_code,422)
            result=api.post("/api/auth/register",json={"username":name,"password":password})
            self.assertEqual(result.status_code,201)
            recovery=result.json()["recovery_code"]
            store=server.app.state.online_store
            uid=store.get_credentials(name)["id"]
            with store.cursor() as cur:
                for i in range(23):
                    rid=uuid.uuid4().hex
                    cur.execute("""INSERT INTO osint_runs(id,owner_id,task,model_id,status,execution_mode,search_strategy,created_at,updated_at)
                                   VALUES(%s,%s,%s,'fixture','done','quick','legacy',NOW(6),NOW(6))""",(rid,uid,str(i)))
                    cur.execute("INSERT INTO travel_requests VALUES(%s,%s,%s,%s,'{}','used')",(rid,uid,rid,"0"*64))
            pages=[api.get("/api/travel/history",params={"page":i}).json() for i in [1,2,3,999]]
            self.assertEqual([len(p["runs"]) for p in pages],[10,10,3,3])
            self.assertEqual(len({r["id"] for p in pages[:3] for r in p["runs"]}),23)
            self.assertEqual(pages[-1]["page"],3)
            api.post("/api/auth/logout")
            def attempt(pw,captcha=""):
                return api.post("/api/auth/login",json={"username":name,"password":pw,"captcha":captcha})
            self.assertEqual(attempt("wrong").status_code,401)
            self.assertIsInstance(attempt("wrong").json()["detail"],str)
            with patch("account_security.secrets.choice",return_value="2"):
                self.assertTrue(attempt("wrong").json()["detail"]["captcha_required"])
                self.assertEqual(attempt(password).status_code,401)
                self.assertEqual(attempt(password,"33333").status_code,401)
                self.assertEqual(attempt(password,"22222").status_code,200)
            reset={"username":name,"recovery_code":recovery,"new_password":"替换后的密码"}
            self.assertEqual(api.post("/api/auth/reset-password",json=reset).status_code,200)
            self.assertEqual(api.get("/api/auth/me").status_code,401)
            self.assertEqual(api.post("/api/auth/reset-password",json=reset).status_code,400)
            self.assertEqual(attempt(password).status_code,401)
            self.assertEqual(attempt(reset["new_password"]).status_code,200)
            self.assertEqual(api.post("/api/auth/close-account",json={"password":"wrong"}).status_code,403)
            self.assertEqual(api.post("/api/auth/close-account",json={"password":reset["new_password"]}).status_code,200)
            self.assertIsNone(store.get_credentials(name))
            self.assertEqual(api.get("/api/travel/history").status_code,401)
