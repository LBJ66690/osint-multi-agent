import argparse
import asyncio
from datetime import date, datetime, timedelta
from functools import partial
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from travel_tools import CHINA, invoke, route, forecast


async def main(start):
    ok = True
    for key in ("fanjingshan-east","fanjingshan-west","江口"):
        steps = []
        entry = {"query":key,"steps":steps,"data_mode":"live"}
        call = partial(invoke,trace=steps)
        try:
            if key=="江口":
                entry["result"]=await forecast("fanjingshan-east",start,start+timedelta(days=2),call=call)
            else:
                entry["result"]=await route("铜仁站",key,call=call)
        except Exception as exc:
            while hasattr(exc,"exceptions"):
                exc=exc.exceptions[0]
            entry["error"]=type(exc).__name__ + " " + str(exc)[:120]
            ok=False
        print(json.dumps(entry,ensure_ascii=False),flush=True)
    return 0 if ok else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--start",type=date.fromisoformat,default=datetime.now(CHINA).date())
    sys.exit(asyncio.run(main(parser.parse_args().start)))
