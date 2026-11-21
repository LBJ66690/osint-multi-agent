import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import config
import server
import travel_cards
import travel_plan


class ReportFormatAPI(unittest.TestCase):
    def test_formats_and_same_key(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("REPORT_DIR", "CHECKPOINT_DIR"):
                self.enterContext(patch.object(config, name, Path(folder)))
            self.enterContext(patch.object(config, "DASHSCOPE_API_KEY", ""))
            for name in ("route", "forecast", "retrieve"):
                self.enterContext(patch.object(travel_plan, name, AsyncMock(side_effect=ValueError("fixture"))))
            generate = AsyncMock(side_effect=lambda model, facts: json.dumps(facts))
            finalize = travel_cards.finalize

            async def replay(state, plan, catalog, checkpoint):
                await finalize(state, plan, catalog, checkpoint, generate)

            self.enterContext(patch.object(travel_cards, "finalize", replay))
            create = json.loads(Path("contracts/travel/v1/fixtures/create-run.json").read_text(encoding="utf8"))
            create["trip"]["ticket_fields"] = {"arrival_station": "铜仁站"}
            with TestClient(server.app) as api:
                for fmt in (None, "text", "cards"):
                    with self.subTest(fmt=fmt):
                        body = create if fmt is None else dict(create, report_format=fmt)
                        headers = {"Idempotency-Key": "format-test-" + str(fmt) + Path(folder).name}
                        run = api.post("/api/runs", json=body, headers=headers)
                        self.assertEqual(run.status_code, 200, run.text)
                        rid = run.json()["run_id"]
                        self.addCleanup(server.runs.pop, rid, None)
                        self.assertEqual(api.post("/api/runs", json=body, headers=headers).json()["run_id"], rid)
                        altered = dict(body, report_format="text" if fmt == "cards" else "cards")
                        self.assertEqual(api.post("/api/runs", json=altered, headers=headers).status_code, 409)
                        for _ in range(90):
                            detail = api.get("/api/runs/" + rid).json()
                            if detail["status"] in server.FINAL: break
                            time.sleep(.05)
                        self.assertEqual(detail["status"], "done", detail["summary"].get("error"))
                        self.assertEqual(detail.get("rendered_format"), fmt)
                        self.assertEqual("card_report" in detail, fmt == "cards")
                        self.assertEqual(api.get(f"/api/runs/{rid}/report.md").status_code, 200)
                        self.assertEqual(generate.await_count, 1 if fmt == "cards" else 0)
                        if fmt == "cards":
                            server.runs.pop(rid)
                            server._restore_history_from_disk()
                            restored = api.get("/api/runs/" + rid).json()
                            self.assertEqual(restored["card_report"], detail["card_report"])
                            self.assertEqual(generate.await_count, 1)

