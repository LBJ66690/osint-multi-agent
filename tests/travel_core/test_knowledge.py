import json
from pathlib import Path
import os
import unittest
from unittest.mock import patch
import httpx
import travel_plan


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,handler):
        original=httpx.AsyncClient
        def client(**kw):
            return original(transport=httpx.MockTransport(handler),**kw)
        with patch.dict(os.environ,TRAVEL_KB_BASE_URL="http://fixture",TRAVEL_KB_TOKEN="fixture"),patch.object(httpx,"AsyncClient",client):
            return await travel_plan.retrieve("查询",{"poi_ids":["fanjingshan-east"],"start_date":"2026-11-20","end_date":"2026-11-22"})

    async def test_snapshot_and_conflict(self):
        fixture=json.loads(Path("contracts/travel/v1/fixtures/retrieve-response.json").read_text(encoding="utf-8"))
        fixture["hits"].append({**fixture["hits"][0],"doc_id":"official","source_type":"official_notice","excerpt":"东线停运"})
        def respond(req):
            self.assertEqual(req.headers["Authorization"],"Bearer fixture")
            self.assertEqual(json.loads(req.content)["trip_dates"],["2026-11-20","2026-11-22"])
            return httpx.Response(200,json=fixture)
        self.assertEqual((await self.exercise(respond))["hits"],fixture["hits"])

    async def test_empty(self):
        self.assertEqual((await self.exercise(lambda req:httpx.Response(200,json={"corpus_version":"demo","hits":[]})))["hits"],[])

    async def test_timeout(self):
        def timeout(req): raise httpx.ReadTimeout("fixture")
        with self.assertRaises(httpx.ReadTimeout): await self.exercise(timeout)

    async def test_service_unavailable(self):
        with self.assertRaises(httpx.HTTPStatusError):
            await self.exercise(lambda req:httpx.Response(503))
