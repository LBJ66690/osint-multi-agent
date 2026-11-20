"""Run the same travel executor with frozen synthetic tools. Optional paired real model calls."""
import argparse
import asyncio
from datetime import date,datetime,timedelta
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import config
from travel_plan import execute
from travel_tools import catalog

HERE=Path(__file__).resolve().parent


async def evaluate(case,variant,use_model):
    start=date.fromisoformat(case["start_date"])
    end=date.fromisoformat(case["end_date"])
    scenario=case["scenario"]
    poi=case["poi_id"]
    trip={k:case[k] for k in ("origin","travelers","start_date","end_date")}
    trip.update(poi_ids=[poi],timezone="Asia/Shanghai",budget_scope="group",budget_fen=200000,include_round_trip=True,
                ticket_fields={"date":str(start),"arrival_time":"10:30","arrival_station":"铜仁站","latest_entry":"11:00"})
    state=SimpleNamespace(id=uuid.uuid4().hex,task=case["task"],model_id=config.resolve_model(None),travel={"trip":trip,"attachment_ids":[]},
        summary={},store=None,events=[],travel_data_mode="fixture",travel_clock=datetime.fromisoformat(case["clock"]),travel_all_skills=variant=="all-skills",
        travel_full_evidence=variant=="full-evidence")
    cards=catalog()
    async def leg(station,gate,profile):
        if scenario=="上游超时":raise TimeoutError()
        return {"origin":station,"poi_id":gate,"profile":profile,"duration_s":3600,"distance_m":40000,
                "label":"驾车估算","notice":"接驳班次待核对"}
    async def weather(gate,start,end):
        if scenario=="上游超时":raise TimeoutError()
        return {"location":cards[gate]["county"],"kind":"forecast","days":[{"date":str(start+timedelta(days=i)),
                "precipitation_probability_max":80} for i in range((end-start).days+1)]}
    async def kb(task,trip):
        return {"corpus_version":"fixture-1","hits":[{"poi_id":poi,"doc_id":"fixture-notice","chunk_id":"c1",
                "title":"景区公告样例","excerpt":"索道停运","valid_from":str(start),"valid_to":str(end),
                "source_type":"official_notice","rights":"team_fixture"}]}
    async def emit(ev):state.events.append(ev)
    start_time=time.monotonic()
    summary=await execute(state,emit,leg,weather,kb)
    plan=summary["trip_plan"]
    ids=set(plan["evidence_ids"])
    checks={"dates":[d["date"] for d in plan["days"]]==[str(start),str(end)],
            "budget_unknown":plan["budget"]["upper_fen"] is None,
            "citations":all(eid in ids for d in plan["days"] for eid in d["evidence_ids"]),
            "reservation":scenario=="上游超时" or any("晚于预约截止" in s for s in plan["unresolved"]),
            "weather":bool(plan["weather_alternatives"])==(scenario!="上游超时"),
            "booking":bool(plan["booking_entries"]) and all(x["poi_id"] in cards for x in plan["booking_entries"])}
    if scenario=="带适用日期的景区公告" and poi!="fanjingshan":
        checks["notice"]=any("停运" in t for d in plan["days"] for t in d["activities"])
    if scenario=="上游超时":
        checks["degraded"]=any(e["type"]=="tool_error" for e in state.events)
    model_events=[e for e in state.events if e["type"]=="model_response"]
    usage={}
    for k in ("prompt","completion","total"):
        values=[e.get("usage",{}).get(k) for e in model_events]
        usage[k]=sum(values) if values and all(type(v)==int for v in values) else None
    return {"loaded_skills":summary.get("loaded_model_skills",[]),"case_id":case["case_id"],"variant":variant,"passed":all(checks.values()),"failed":[k for k,v in checks.items() if not v],
            "checks":checks,"evidence_ids":sorted(ids),"elapsed_s":round(time.monotonic()-start_time,3),
            "tool_calls":sum(e["type"]=="tool_call" for e in state.events),"model":state.model_id if use_model else None,
            "usage":usage,"resources":summary["resource_usage"],"mode":"real_model_fixture_tools" if use_model else "fixture"}


async def main(args):
    if args.real_model and not config.DASHSCOPE_API_KEY: raise SystemExit("请配置模型密钥后运行真实模型对照")
    if not args.real_model: config.DASHSCOPE_API_KEY=""
    cases=json.loads((HERE/"replay_cases.json").read_text(encoding="utf-8"))
    output=config.OUTPUT_DIR/"travel_eval"
    output.mkdir(exist_ok=True)
    config._set_output_dir(output/"runs")
    results=[]
    variants=["selected","all-skills","full-evidence"] if args.real_model else ["selected"]
    for case in cases:
        if args.split and case["split"]!=args.split:continue
        for v in variants:results.append(await evaluate(case,v,args.real_model))
    target=output/("model.jsonl" if args.real_model else "replay.jsonl")
    target.write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in results),encoding="utf-8")
    print(json.dumps({"file":str(target),"cases":len(results),"passed":sum(r["passed"] for r in results),
                      "p50_seconds":statistics.median(r["elapsed_s"] for r in results),
                      "p95_seconds":statistics.quantiles([r["elapsed_s"] for r in results],n=20)[18]},ensure_ascii=False))
    return 0 if all(r["passed"] for r in results) else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--real-model",action="store_true")
    parser.add_argument("--split",choices=["dev","acceptance"])
    sys.exit(asyncio.run(main(parser.parse_args())))
