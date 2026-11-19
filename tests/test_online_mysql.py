"""Exercise the authenticated API against a dedicated disposable MySQL schema."""
from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

import config
import report_chat
import server
import test_execution
from schemas import QuickSynthesis


@unittest.skipUnless(os.getenv("OSINT_TEST_MYSQL") == "1", "requires a disposable MySQL test schema")
class MySQLOnlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_isolation_pipeline_persistence_and_restart(self):
        origin = "http://127.0.0.1:8765"
        env = {
            "OSINT_STORAGE": "mysql",
            "OSINT_SECURE_COOKIE": "0",
            "OSINT_PUBLIC_ORIGIN": origin,
            "OSINT_REGISTRATION_ENABLED": "1",
            "OSINT_SIGNUP_CODE": "",
            "OSINT_BAILIAN_FREE_ONLY_CONFIRMED": "1",
        }
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 50000))
        users = [f"test_{uuid4().hex[:12]}" for _ in range(2)]
        example_password = "synthetic-passphrase-76"
        fixture = test_execution.Fixture()
        await fixture.asyncSetUp()
        self.addAsyncCleanup(fixture.asyncTearDown)
        with patch.dict(os.environ, env), patch.object(config, "DASHSCOPE_API_KEY", "fixture-key"), \
             patch.object(report_chat, "generate_answer", new=AsyncMock(return_value=("测试回答", None))):
            async with (httpx.AsyncClient(transport=transport, base_url=origin, headers={"Origin": origin}) as first,
                        httpx.AsyncClient(transport=transport, base_url=origin, headers={"Origin": origin}) as second):
                async with server.app.router.lifespan_context(server.app):
                    store = server.app.state.online_store
                    for setting, directory in (("PLAN_DIR", "plans"), ("EVIDENCE_DIR", "evidence"),
                                               ("REPORT_DIR", "reports"), ("CHECKPOINT_DIR", "checkpoints")):
                        setattr(config, setting, fixture.root / directory)
                    self.assertEqual((await first.get("/")).headers.get("location"), "/login")
                    self.assertEqual((await first.get("/api/runs")).status_code, 401)
                    self.assertEqual((await first.get("/api/auth/options")).json()["registration_enabled"], True)
                    cross = await first.post("/api/auth/register", headers={"Origin": "https://other.invalid"},
                                             json={"username": users[0], "password": example_password})
                    self.assertEqual(cross.status_code, 403)
                    for client, username in zip((first, second), users):
                        response = await client.post("/api/auth/register", json={
                            "username": username, "password": example_password,
                        })
                        self.assertEqual(response.status_code, 201, response.text)
                        self.assertIn("HttpOnly", response.headers["set-cookie"])
                        self.assertIn("SameSite=lax", response.headers["set-cookie"])
                    duplicate = await first.post("/api/auth/register", json={
                        "username": users[0].upper(), "password": example_password,
                    })
                    self.assertEqual(duplicate.status_code, 409)
                    first_token = first.cookies.get("osint_session")
                    with store.cursor() as cursor:
                        cursor.execute("SELECT password_hash FROM osint_users WHERE username = %s", (users[0],))
                        self.assertTrue(cursor.fetchone()["password_hash"].startswith("$argon2id$"))
                        cursor.execute("SELECT token_digest FROM osint_sessions WHERE token_digest = %s",
                                       (hashlib.sha256(first_token.encode("ascii")).digest(),))
                        self.assertEqual(len(cursor.fetchone()["token_digest"]), 32)
                    first_models = (await first.get("/api/models")).json()
                    self.assertEqual(len(first_models["models"]), 6)
                    rejected = await first.post("/api/runs", json={"task": "测试", "model_id": "other-llm"})
                    self.assertEqual(rejected.status_code, 400)
                    response = await first.post("/api/runs", json={
                        "task": "预约与停车：保存到 MySQL", "execution_mode": "quick", "auto_confirm": True,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    run_id = response.json()["run_id"]
                    paths = [f"/api/runs/{run_id}", f"/api/runs/{run_id}/events",
                             f"/api/runs/{run_id}/report.md", f"/api/runs/{run_id}/report-chat?report_type=a",
                             f"/api/reports/{run_id}/file_{run_id}.md"]
                    for path in paths:
                        self.assertEqual((await second.get(path)).status_code, 404, path)
                    self.assertEqual((await second.post(f"/api/runs/{run_id}/resume", json={})).status_code, 404)
                    self.assertEqual((await second.post(f"/api/runs/{run_id}/cancel", json={})).status_code, 404)
                    self.assertEqual((await second.get("/api/runs")).json()["runs"], [])

                    state = server.runs[run_id]
                    async with asyncio.timeout(10):
                        while state.pending_sources is None:
                            await asyncio.sleep(0.02)
                    denied_source = await first.post(f"/api/runs/{run_id}/sources", json={
                        "request_id": state.pending_sources["request_id"], "sources": ["bailian"],
                    })
                    self.assertEqual(denied_source.status_code, 400)
                    choice = await first.post(f"/api/runs/{run_id}/sources", json={
                        "request_id": state.pending_sources["request_id"], "sources": ["parallel"],
                    })
                    self.assertEqual(choice.status_code, 200)
                    await asyncio.wait_for(state.task_obj, 10)
                    self.assertEqual(state.status, "done", state.summary)
                    self.assertFalse(list(fixture.root.rglob(f"*{run_id}*")))
                    detail = (await first.get(f"/api/runs/{run_id}")).json()
                    report = detail["reports"][0]
                    self.assertTrue(report["path"].startswith("reports/"))
                    self.assertIn("测试报告", report["markdown"])
                    self.assertEqual(store.load_checkpoint(run_id)["status"], "done")
                    self.assertTrue(store.load_events(run_id))
                    chat = (await first.get(f"/api/runs/{run_id}/report-chat",
                                            params={"report_type": report["type"]})).json()
                    ask = await first.post(f"/api/runs/{run_id}/report-chat", json={
                        "report_type": report["type"], "report_version": chat["report_version"],
                        "question": "有哪些依据？", "request_id": "test-request-01", "model_id": response.json()["model_id"],
                    })
                    self.assertEqual(ask.status_code, 200, ask.text)
                    self.assertEqual(ask.json()["turns"][0]["answer"], "测试回答")

                async with server.app.router.lifespan_context(server.app):
                    self.assertFalse(server.runs.get(run_id))
                    self.assertEqual((await first.get("/api/auth/me")).json()["username"], users[0])
                    self.assertEqual((await first.get("/api/runs")).json()["runs"][0]["id"], run_id)
                    self.assertEqual((await second.get(f"/api/runs/{run_id}")).status_code, 404)
                    detail = (await first.get(f"/api/runs/{run_id}")).json()
                    self.assertEqual(detail["status"], "done")
                    self.assertIn("测试报告", (await first.get(f"/api/runs/{run_id}/report.md")).text)
                    file = Path(report["path"]).name
                    restored = await first.get(f"/api/reports/{run_id}/{file}")
                    self.assertEqual(restored.json()["content"], report["markdown"])
                    self.assertIn("stream_end", (await first.get(f"/api/runs/{run_id}/events")).text)
                    chat = (await first.get(f"/api/runs/{run_id}/report-chat",
                                            params={"report_type": report["type"]})).json()
                    self.assertEqual(chat["turns"][0]["answer"], "测试回答")
                    closed = await first.post("/api/auth/logout", json={})
                    self.assertEqual(closed.status_code, 200)
                    rejected = await first.get("/api/runs", headers={"Cookie": f"osint_session={first_token}"})
                    self.assertEqual(rejected.status_code, 401)
                    wrong = await first.post("/api/auth/login", json={"username": users[0], "password": "wrong"})
                    self.assertEqual(wrong.status_code, 401)
                    logged = await first.post("/api/auth/login", json={"username": users[0], "password": example_password})
                    self.assertEqual(logged.status_code, 200)
                    self.assertNotEqual(first.cookies.get("osint_session"), first_token)

    async def test_failed_run_resumes_from_mysql_without_repeating_search(self):
        origin = "http://127.0.0.1:8765"
        env = {
            "OSINT_STORAGE": "mysql", "OSINT_SECURE_COOKIE": "0", "OSINT_PUBLIC_ORIGIN": origin,
            "OSINT_REGISTRATION_ENABLED": "1", "OSINT_SIGNUP_CODE": "",
            "OSINT_BAILIAN_FREE_ONLY_CONFIRMED": "1",
        }
        fixture = test_execution.Fixture()
        await fixture.asyncSetUp()
        self.addAsyncCleanup(fixture.asyncTearDown)
        fixture.fail_schema = QuickSynthesis
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.2", 50000))
        with patch.dict(os.environ, env), patch.object(config, "DASHSCOPE_API_KEY", "fixture-key"):
            async with httpx.AsyncClient(transport=transport, base_url=origin, headers={"Origin": origin}) as client:
                async with server.app.router.lifespan_context(server.app):
                    registration = await client.post("/api/auth/register", json={
                        "username": f"resume_{uuid4().hex[:12]}", "password": "synthetic-passphrase-76",
                    })
                    self.assertEqual(registration.status_code, 201, registration.text)
                    response = await client.post("/api/runs", json={
                        "task": "测试恢复与已保存资料", "execution_mode": "quick", "auto_confirm": True,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    run_id = response.json()["run_id"]
                    state = server.runs[run_id]
                    async with asyncio.timeout(10):
                        while state.pending_sources is None:
                            await asyncio.sleep(0.02)
                    chosen = await client.post(f"/api/runs/{run_id}/sources", json={
                        "request_id": state.pending_sources["request_id"], "sources": ["parallel"],
                    })
                    self.assertEqual(chosen.status_code, 200, chosen.text)
                    await asyncio.wait_for(state.task_obj, 10)
                    self.assertEqual(state.status, "error")
                    store = server.app.state.online_store
                    checkpoint = store.load_checkpoint(run_id)
                    self.assertEqual(checkpoint["completed_stages"], ["planning", "collecting"])
                    previous_searches = list(fixture.searches)

                fixture.fail_schema = None
                async with server.app.router.lifespan_context(server.app):
                    response = await client.post(f"/api/runs/{run_id}/resume", json={})
                    self.assertEqual(response.status_code, 200, response.text)
                    state = server.runs[run_id]
                    await asyncio.wait_for(state.task_obj, 10)
                    self.assertEqual(state.status, "done", state.summary)
                    self.assertEqual(fixture.searches, previous_searches)
                    self.assertTrue(any(event["type"] == "resumed" for event in state.events))
