"""Read-only stdio providers for the travel executor."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))
STATIONS = {
    "铜仁站": (27.74332, 109.19970, "https://www.openstreetmap.org/node/1681825151"),
    "铜仁南站": (27.28245, 108.97131, "https://www.openstreetmap.org/node/2953807111"),
}
PACKAGES = {"route": "osm-mcp", "weather_forecast": "open-meteo-mcp-server"}


def catalog():
    path = Path(os.getenv("TRAVEL_POI_CATALOG", Path(os.getenv("TRAVEL_STATIC_DIR",ROOT / "static")) / "travel/data/pois.json"))
    return {c["poi_id"]: c for c in json.loads(path.read_text(encoding="utf-8"))}


async def invoke(tool, arguments, trace=None):
    steps = trace if trace is not None else []
    railway = tool in {"get-station-code-by-names","get-tickets","get-interline-tickets"}
    package = "12306-mcp" if railway else PACKAGES[tool]
    entry = ROOT / "scripts/travel_eval/mcp/node_modules" / package / ("build/index.js" if railway else "dist/index.js")
    env_keys = {"HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","SSL_CERT_FILE","SSL_CERT_DIR",
                "NODE_EXTRA_CA_CERTS","NODE_USE_SYSTEM_CA","REQUESTS_CA_BUNDLE","CURL_CA_BUNDLE","OSRM_BASE_URL"}
    env = {k:v for k,v in os.environ.items() if k.upper() in env_keys}
    env={k:v for k,v in env.items() if v}
    env.update(TRANSPORT="stdio", NODE_USE_ENV_PROXY="1")
    params = StdioServerParameters(command=os.getenv("TRAVEL_NODE","node"), args=[str(entry)], env=env)
    async def query():
        with open(os.devnull, "w") as err:
            async with stdio_client(params, errlog=err) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    steps.append("initialize")
                    tools = await session.list_tools()
                    if tool not in {item.name for item in tools.tools}:
                        raise ValueError("工具清单缺少所需能力")
                    steps.append("tools/list")
                    response = await session.call_tool(tool, arguments)
                    steps.append("tools/call")
                    if response.isError:
                        raise ValueError("上游未返回有效业务数据")
                    return response.structuredContent or json.loads(response.content[0].text)
    return await asyncio.wait_for(query(), float(os.getenv("TRAVEL_MCP_TIMEOUT","35")))


async def route(station, gate, profile="car", call=invoke):
    point = catalog().get(gate, {}).get("coordinates", {})
    if station not in STATIONS or point.get("system") != "WGS84" or point.get("precision") != "mapped_entrance":
        raise ValueError("请确认车站与景区入口，缺少已核对坐标")
    if profile not in ("foot","car"):
        raise ValueError("请确认驾车或步行")
    lat,lon,source = STATIONS[station]
    raw = await call("route", {"waypoints":[f"{lat},{lon}",f"{point['latitude']},{point['longitude']}"],"profile":profile})
    if raw.get("profile") != profile or any(type(raw.get(k)) != int or raw[k]<=0 for k in ("distance_m","duration_s")):
        raise ValueError("路线数据不完整")
    return {"origin":station,"poi_id":gate,"profile":profile,"distance_m":raw["distance_m"],"duration_s":raw["duration_s"],
            "label":"驾车估算" if profile=="car" else "步行估算","sources":[source,point["source_url"]],
            "notice":"接驳车班次和末班时间以运营方公告为准"}


async def forecast(poi_id, start, end, call=invoke):
    card = catalog().get(poi_id)
    if not card or not 0 <= (end-start).days < 16:
        raise ValueError("请确认目的地和预报日期")
    point = card["coordinates"]
    if point["system"] != "WGS84":
        raise ValueError("请核对天气查询坐标系")
    raw = await call("weather_forecast", {"latitude":point["latitude"],"longitude":point["longitude"],
        "timezone":"Asia/Shanghai","start_date":start.isoformat(),"end_date":end.isoformat(),
        "daily":["precipitation_probability_max","weather_code"]})
    dates = [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    daily = raw["daily"]
    probs = daily["precipitation_probability_max"]
    if (raw.get("timezone")!="Asia/Shanghai" or daily["time"]!=dates or len(probs)!=len(dates)
        or raw.get("daily_units",{}).get("precipitation_probability_max")!="%"
        or not all(type(p) in (int,float) and 0<=p<=100 for p in probs)):
        raise ValueError("预报日期、时区或概率无效")
    return {"location":card["county"],"kind":"forecast","timezone":"Asia/Shanghai",
            "days":[{"date":d,"precipitation_probability_max":p} for d,p in zip(dates,probs)],
            "as_of":datetime.now(CHINA).isoformat(timespec="seconds")}

async def rail(origin,destination,day):
    names=[s.removesuffix("站") for s in (origin,destination)]
    stations=await invoke("get-station-code-by-names",{"stationNames":"|".join(names)})
    if any(not stations.get(s,{}).get("station_code") for s in names):
        raise ValueError("请核对铁路车站")
    args={"date":day,"fromStation":stations[names[0]]["station_code"],"toStation":stations[names[1]]["station_code"],
          "limitedNum":4,"format":"json"}
    rows=await invoke("get-tickets",args)
    if not isinstance(rows,list):
        raise ValueError("铁路返回格式无效")
    return {"date":day,"from":origin,"to":destination,"trains":[
        {k:r.get(k) for k in ("start_train_code","from_station","to_station","start_date","arrive_date","start_time","arrive_time","prices")}
        for r in rows[:4] if r.get("from_station")==names[0] and r.get("to_station")==names[1] and r.get("start_date")==day]}


async def flight(dep,arr,day):
    from mcp.client.streamable_http import streamablehttp_client
    key=os.getenv("VARIFLIGHT_API_KEY")
    if not key:
        raise ValueError("飞常准尚未配置")
    async def fetch():
        async with streamablehttp_client("https://ai.variflight.com/servers/aviation/mcp",
                headers={"X-API-Key":key},timeout=10,sse_read_timeout=20) as (r,w,_):
            async with ClientSession(r,w) as s:
                await s.initialize()
                if "searchFlightsByDepArr" not in {t.name for t in (await s.list_tools()).tools}:
                    raise ValueError("航空查询未提供")
                res=await s.call_tool("searchFlightsByDepArr",{"dep":dep,"arr":arr,"date":day,"limit":4,"detail":"summary"})
                if res.isError: raise ValueError("航班待查")
                return res.structuredContent or json.loads(res.content[0].text)
    return await asyncio.wait_for(fetch(),35)
