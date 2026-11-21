"""Versioned departure directory shared by search and trip validation."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

TravelMode = Literal["high_speed","rail","air","car","coach","tour"]
KINDS = {"high_speed":"station","rail":"station","air":"airport","car":"city","coach":"city","tour":"city"}

@lru_cache(maxsize=1)
def directory():
    path = Path(__file__).parent / "static/travel/data/departures.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["version"], {row["id"]:row for row in data["items"]}

def search(mode, keyword):
    version, rows = directory()
    q = keyword.strip().casefold()
    items = (r for r in rows.values() if r["kind"] == KINDS[mode] and
             any(q in r[k].casefold() for k in ("id","name","city","province")))
    from itertools import islice
    return {"version":version,"items":list(islice(items,80))}
