"""Run the three local itinerary acceptance paths against an isolated test database."""
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
import server
from travel_tools import CHINA


def run():
    if os.getenv("OSINT_TEST_MYSQL") != "1" or not os.getenv("OSINT_DB_NAME", "").startswith("travel_test_"):
        raise SystemExit("请明确指定专用 travel_test_ 数据库及 OSINT_TEST_MYSQL=1")
    origin = os.environ["OSINT_PUBLIC_ORIGIN"]
    start = datetime.now(CHINA).date() + timedelta(days=1)
    cases = [
        ("花溪周末", "osm-way-511605339", "high_speed", "rail:CWQ", 4, 1),
        ("万峰林自驾", "wanfenglin", "car", "city:5201", 2, 1),
        ("安顺顾府街", "osm-way-289490112", "car", "city:5201", 1, 0),
    ]
    for name, poi, mode, departure, people, nights in cases:
        with TestClient(server.app, base_url=origin, headers={"Origin":origin}) as api:
            account = {"username":"t"+uuid.uuid4().hex[:12], "password":secrets.token_urlsafe(20)}
            assert api.post("/api/auth/register", json=account).status_code == 201
            trip = {"poi_ids":[poi], "travel_mode":mode, "departure_id":departure, "travelers":people,
                    "start_date":str(start), "end_date":str(start+timedelta(days=nights)),
                    "budget_fen":250000, "budget_scope":"group", "include_round_trip":True}
            draft = api.post("/api/travel/drafts/preview", json={"task":name,"overrides":trip})
            assert draft.status_code == 200 and not draft.json()["missing_fields"]
            payload = {"task":name, "domain":"guizhou_travel", "trip":trip, "report_format":"cards"}
            headers = {"Idempotency-Key":uuid.uuid4().hex}
            response = api.post("/api/runs", json=payload, headers=headers)
            assert response.status_code == 200, response.text
            rid = response.json()["run_id"]
            assert api.post("/api/runs", json=payload, headers=headers).json()["run_id"] == rid
            for _ in range(200):
                detail = api.get("/api/runs/"+rid).json()
                if detail["status"] in server.FINAL: break
                time.sleep(1)
            assert detail["status"] == "done" and detail["reports"][0]["markdown"]
            assert api.get("/api/travel/credits").json() == {"available":0,"used":1,"reserved":0}
            plan = detail["trip_plan"]
            assert ("住宿" in {row["label"] for row in plan["budget"]["lines"]}) == bool(nights)
            # Drop the live state to exercise rehydration from MySQL.
            server.runs.pop(rid)
            reopened = api.get("/api/runs/"+rid).json()
            assert reopened["reports"] == detail["reports"] and reopened["rendered_format"] == detail["rendered_format"]
            print(name, rid, reopened["rendered_format"], "weather",len(plan["weather"]),
                  "rail", sum(len(v) for v in plan["railway"].values()), flush=True)


if __name__ == "__main__":
    run()

