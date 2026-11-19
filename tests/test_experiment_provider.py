"""Radeon 请求协议、启动隔离及无百炼回退的回归测试。"""
import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

import httpx
from openai import APIConnectionError, AsyncOpenAI

import agents
import config
import models
import pipeline
import search_sources
import sag_evidence
import server
from schemas import SubQuestion


class ExperimentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sag_read_only_index_excludes_notes_and_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "sag.sqlite"
            scope = root / "approved"
            scope.mkdir()
            with closing(sqlite3.connect(index)) as db, db:
                db.executescript("""
                    PRAGMA user_version=9;
                    CREATE TABLE documents(id INTEGER, path TEXT, hash TEXT);
                    CREATE TABLE chunks(document_id INTEGER, start_line INTEGER, end_line INTEGER);
                    CREATE TABLE notes(document_id INTEGER);
                    INSERT INTO notes VALUES(2);
                """)
                for identifier, path in enumerate(
                        [scope/"original.md", scope/"chat.md", scope/"changed.md", root/"outside.md"], 1):
                    body = b"blue trail is closed until further notice"
                    path.write_bytes(body)
                    db.execute("INSERT INTO documents VALUES(?,?,?)", (identifier, str(path), hashlib.sha256(body).hexdigest()))
                    db.execute("INSERT INTO chunks VALUES(?,1,1)", (identifier,))
                (scope/"changed.md").write_bytes(b"blue trail is open")
            rows = await sag_evidence.search(index, scope, SubQuestion(question="trail?", search_queries=["blue trail"]),
                                            "fixture", 8766)
            self.assertEqual(len(rows), 1)
            self.assertIn("original.md", rows[0].title)
            self.assertNotIn(directory, rows[0].model_dump_json())

    async def test_model_http_client_blocks_historical_radeon_before_network(self):
        clients = []

        def reject(_request):
            self.fail("历史 Radeon 请求到达了网络传输层")

        def sdk(**kwargs):
            real_http = kwargs.pop("http_client")
            clients.append(real_http)
            guarded = httpx.AsyncClient(transport=httpx.MockTransport(reject), event_hooks=real_http.event_hooks)
            client = AsyncOpenAI(http_client=guarded, **kwargs)
            clients.append(client)
            return client

        try:
            with patch.multiple(config, EXPERIMENT_MODE=True, RADEON_API_KEY="fixture-radeon"), \
                 patch.object(agents, "AsyncOpenAI", side_effect=sdk):
                agents.make_chat_client("qwen3.8-flash")
                with self.assertRaises(APIConnectionError) as caught:
                    await clients[-1].chat.completions.create(
                        model=config.RADEON_MODEL, messages=[{"role": "user", "content": "测试"}],
                    )
                self.assertIn("仅允许", str(caught.exception.__cause__))
        finally:
            for client in clients:
                if isinstance(client, AsyncOpenAI):
                    await client.close()
                else:
                    await client.aclose()

    async def test_radeon_mode_lists_only_experiment_model_and_public_search(self):
        with patch.multiple(config, EXPERIMENT_MODE=True, RADEON_API_KEY="fixture-radeon",
                            DASHSCOPE_API_KEY="fixture-bailian"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local") as client:
                health = (await client.get("/api/health")).json()
                catalog = (await client.get("/api/models")).json()
                sources = (await client.get("/api/search-sources")).json()
                invalid = await client.post("/api/runs", json={"task": "实验任务",
                    "role_models": {"planner": "qwen3.8-flash"}})
            self.assertEqual(health["model_provider"], "amd-radeon")
            self.assertTrue(health["experiment_mode"] and health["has_key"])
            self.assertEqual([entry["id"] for entry in catalog["models"]], [config.RADEON_MODEL])
            for roles in catalog["role_profiles"].values():
                self.assertEqual(set(roles.values()), {config.RADEON_MODEL})
            self.assertEqual({entry["id"] for entry in sources["sources"]}, search_sources.FREE_IDS)
            self.assertEqual(invalid.status_code, 400)
            with self.assertRaises(ValueError):
                search_sources.validate_sources(["parallel", "bailian"])
            with self.assertRaises(RuntimeError):
                async with agents.websearch_mcp_session():
                    self.fail("实验进入了百炼 WebSearch")
            self.assertNotIn("fixture-radeon", json.dumps([health, catalog]))
            self.assertEqual(config.redact_secret("fixture-radeon fixture-bailian"), "[REDACTED] [REDACTED]")
            with patch.object(config, "RADEON_API_KEY", ""):
                self.assertFalse((await server.health())["has_key"])
        self.assertIsNone(models.get_model(config.RADEON_MODEL))

    async def test_bailian_checkpoint_cannot_resume_in_experiment_mode(self):
        checkpoint = {"task": "实验任务", "run_id": "fixture"}
        with patch.object(config, "EXPERIMENT_MODE", True), patch.object(pipeline, "load_checkpoint", return_value=checkpoint), \
                patch("pipeline_execution.resume_info"), patch.object(pipeline, "build_agent") as build:
            with self.assertRaisesRegex(ValueError, "模型服务模式"):
                await pipeline.run_pipeline("实验任务", config.RADEON_MODEL, None, None, run_id="fixture", resume=True)
            build.assert_not_called()

    def test_launcher_does_not_prompt_or_start_for_retired_radeon_entry(self):
        import launch

        with patch("config.request_api_key") as prompt, patch("server.main") as service:
            self.assertEqual(launch.main(["--experiment"]), 2)
            prompt.assert_not_called()
            service.assert_not_called()
