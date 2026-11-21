import asyncio
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from travel_tools import forecast,route
from travel_plan import budget


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p=Path(tmp.name)/"pois.json"
        p.write_text(json.dumps([{"poi_id":"gate","coordinates":{"latitude":27.1,"longitude":108.2,
                "system":"WGS84","precision":"mapped_entrance","source_url":"fixture"},"county":"江口县"}]))
        self.enterContext(patch.dict(os.environ,TRAVEL_POI_CATALOG=str(p)))

    async def test_stations_and_coordinate_order(self):
        call=AsyncMock(return_value={"profile":"foot","distance_m":250,"duration_s":100})
        for name,point in [("铜仁站","27.74332,109.1997"),("铜仁南站","27.28245,108.97131")]:
            leg=await route(name,"gate","foot",call)
            self.assertEqual(leg["label"],"步行估算")
            self.assertEqual(call.await_args.args[1]["waypoints"],[point,"27.1,108.2"])

    async def test_no_name_fallback(self):
        call=AsyncMock()
        with self.assertRaises(ValueError):
            await route("铜仁","gate",call=call)
        call.assert_not_awaited()

    async def test_date_and_probability(self):
        raw={"timezone":"Asia/Shanghai","daily_units":{"precipitation_probability_max":"%"},
             "daily":{"time":["2026-11-21"],"precipitation_probability_max":[0]}}
        call=AsyncMock(return_value=raw)
        d=date(2026,11,21)
        result=await forecast("gate",d,d,call)
        self.assertEqual(result["days"][0]["precipitation_probability_max"],0)
        raw["daily"]["time"]=["2026-11-22"]
        with self.assertRaises(ValueError):
            await forecast("gate",d,d,call)

    async def test_home_forecast_accepts_zero_and_caches_by_day(self):
        import travel_api
        from datetime import datetime
        today=datetime.now(travel_api.CHINA).date().isoformat()
        raw={"timezone":"Asia/Shanghai","daily":{"time":[today],"weather_code":[3],
             "temperature_2m_min":[0],"temperature_2m_max":[8],"precipitation_probability_max":[0]}}
        with patch.object(travel_api,"_home_cache",{}), patch.object(travel_api,"invoke",AsyncMock(return_value=raw)) as call:
            result=await travel_api.home_weather()
            self.assertEqual((result["kind"],result["condition"],result["temperature_2m_min"]),("forecast","阴",0))
            self.assertEqual(await travel_api.home_weather(),result)
            call.assert_awaited_once()

    async def test_home_weather_unavailable(self):
        import travel_api
        with patch.object(travel_api,"_home_cache",{}), patch.object(travel_api,"invoke",AsyncMock(side_effect=TimeoutError)):
            result=await travel_api.home_weather()
            self.assertEqual(result["kind"],"unknown")
            self.assertTrue(result["stale"])
            self.assertIsNone(result["as_of"])

    def test_budget(self):
        rows=[{"label":"费用","quantity":q,"lower_fen":p,"upper_fen":p} for q,p in
              [(2,28000),(2,19000),(6,8000),(2,26000)]]
        self.assertEqual(budget(rows,200000)["upper_fen"],194000)
        rows.append({"label":"接驳","quantity":1,"lower_fen":10000,"upper_fen":10000})
        self.assertEqual(budget(rows,200000)["budget_status"],"over")
        rows[-1].update(lower_fen=0,upper_fen=None)
        self.assertIsNone(budget(rows,200000)["upper_fen"])
