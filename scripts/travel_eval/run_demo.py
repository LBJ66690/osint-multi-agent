"""Exercise the real FastAPI travel v1 routes, SSE replay and Markdown download."""
import argparse
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
import server
from travel_tools import CHINA


def run(unavailable=False):
    body=json.loads(Path("contracts/travel/v1/fixtures/create-run.json").read_text(encoding="utf-8"))
    start=datetime.now(CHINA).date()+timedelta(days=1)
    body["trip"].update(start_date=str(start),end_date=str(start+timedelta(days=2)),poi_ids=["fanjingshan-east"],
                        ticket_fields={"arrival_station":"铜仁站"})
    headers={"Idempotency-Key":uuid.uuid4().hex}
    with TestClient(server.app) as api:
        draft=api.post("/api/travel/drafts/preview",json={"task":body["task"],"overrides":body["trip"]})
        assert draft.status_code==200 and not draft.json()["missing_fields"],draft.text
        result=api.post("/api/runs",json=body,headers=headers)
        assert result.status_code==200,result.text
        rid=result.json()["run_id"]
        assert api.post("/api/runs",json=body,headers=headers).json()["run_id"]==rid
        for _ in range(180):
            detail=api.get("/api/runs/"+rid).json()
            if detail["status"] in server.FINAL:
                break
            time.sleep(1)
        assert detail["status"]=="done",detail.get("summary")
        stream=api.get(f"/api/runs/{rid}/events").text
        ids=[int(line[3:]) for line in stream.splitlines() if line.startswith("id:")]
        assert ids and ids==sorted(set(ids))
        replay=api.get(f"/api/runs/{rid}/events",headers={"Last-Event-ID":str(ids[len(ids)//2])}).text
        assert all(int(line[3:])>ids[len(ids)//2] for line in replay.splitlines() if line.startswith("id:"))
        report=api.get(f"/api/runs/{rid}/report.md")
        assert "stream_end" in stream and report.status_code==200
        plan=detail["trip_plan"]
        route_events=[e["type"] for e in detail["events"] if e.get("tool")=="route.leg"]
        if not unavailable:
            assert route_events==["tool_call","tool_result"] and plan["legs"]
        if unavailable:
            assert route_events==["tool_call","tool_error"] and not plan["legs"] and "route.leg 待查" in report.text
        print(json.dumps({"run_id":rid,"status":detail["status"],"route_events":route_events,
                          "legs":plan["legs"],"weather":plan["weather_alternatives"],"unresolved":plan["unresolved"],
                          "report_bytes":len(report.content),"sse_replay":True},ensure_ascii=False))


if __name__=="__main__":
    import os
    parser=argparse.ArgumentParser()
    parser.add_argument("--unavailable",action="store_true")
    args=parser.parse_args()
    if args.unavailable:
        os.environ["OSRM_BASE_URL"]="https://travel-route-unavailable.invalid"
    run(args.unavailable)
