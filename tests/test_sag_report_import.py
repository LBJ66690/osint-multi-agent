import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx

import config
import sag_report_import
import server


class SagReportImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_requires_finished_report_and_saves_only_selected_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            scope = Path(folder).resolve()
            index = scope / "fixture.sqlite"
            with closing(sqlite3.connect(index)) as db, db:
                db.executescript("""
                    PRAGMA user_version=9;
                    CREATE TABLE roots(path TEXT, status TEXT);
                    CREATE TABLE root_exclusions(path TEXT);
                """)
                db.execute("INSERT INTO roots VALUES(?, 'ready')", (str(scope),))
            state = server.RunState("fixture", "qwen3.8-flash")
            state.reports = [{"type": "fixture", "markdown": "# 甲\n保留 [E1](https://example.test/)\n\n# 乙\n不选这段"}]
            url = f"/api/runs/{state.id}/sag-import"
            with patch.dict(server.runs, {state.id: state}), patch.object(config, "EXPERIMENT_MODE", False), \
                    patch("sag_evidence.settings", return_value=(index, scope)), \
                    patch("sag_report_import.is_indexed", return_value=True):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                    response = await client.get(url, params={"report_type": "fixture"})
                    self.assertEqual(response.status_code, 409)
                    state.status = "done"
                    data = (await client.get(url, params={"report_type": "fixture"})).json()
                    self.assertEqual(len(data["sections"]), 2)
                    body = dict(report_type="fixture", report_version=data["report_version"], section_ids=[1])
                    stale = await client.post(url, json={**body, "report_version": "0" * 64})
                    self.assertEqual(stale.status_code, 409)
                    invalid = await client.post(url, json={**body, "section_ids": [99]})
                    self.assertEqual(invalid.status_code, 422)
                    response = await client.post(url, json=body)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual((await client.post(url, json=body)).json(), response.json())
                    with patch("sag_evidence.settings", return_value=None):
                        self.assertEqual((await client.post(url, json=body)).status_code, 409)
            files = list((scope / "osint-report-excerpts").glob("*.md"))
            self.assertEqual(len(files), 1)
            saved = files[0].read_text(encoding="utf-8")
            self.assertIn("保留 [E1](https://example.test/)", saved)
            self.assertNotIn("不选这段", saved)

    def test_markdown_fence_is_preserved_in_preview(self):
        text = "# Heading\n\n```sh\n# Comment in code\n```\n\n# Next\ntext"
        sections = sag_report_import.sections(text)
        self.assertEqual([section["title"] for section in sections], ["Heading", "Next"])
        self.assertIn("# Comment in code", sections[0]["markdown"])
