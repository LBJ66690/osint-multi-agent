import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root=Path(__file__).resolve().parents[2]
    env={k:os.environ[k] for k in ("TRAVEL_POI_CATALOG","TRAVEL_STATIC_DIR","PYTHONIOENCODING") if k in os.environ}
    params=StdioServerParameters(command=sys.executable,args=[str(root/"mcp_local_server.py")],env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            assert any(t.name=="get_scenic_card" for t in (await session.list_tools()).tools)
            result=await session.call_tool("get_scenic_card",{"poi_id":"fanjingshan"})
            assert not result.isError
            card=result.structuredContent or json.loads(result.content[0].text)
            print(json.dumps({"poi_id":card["poi_id"],"name":card["name"],"booking":card["booking"]["type"]},ensure_ascii=False))


if __name__=="__main__":
    asyncio.run(asyncio.wait_for(main(),30))
