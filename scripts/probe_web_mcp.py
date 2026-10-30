"""独立测试通用搜索 MCP：握手、工具发现、真实搜索。无模型、无密钥。"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TARGETS = {
    "parallel": "https://mcp.parallel.ai/mcp",
    "parallel_search": "https://search.parallel.ai/mcp",
    "you": "https://api.you.com/mcp?profile=free",
    "openweb_remote": "https://open-web-search.vercel.app/mcp",
}


async def probe(name, url):
    row = {"id": name, "url": url, "ok": False}
    try:
        async with streamablehttp_client(url, timeout=25, sse_read_timeout=30) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                row["tools"] = [{"name": t.name, "schema": t.inputSchema} for t in listing.tools]
                print(json.dumps({"id": name, "tools": row["tools"]}, ensure_ascii=False), flush=True)
                tool = next((t for t in listing.tools if t.name in {"web_search", "you-search", "search"}), None)
                if not tool:
                    raise RuntimeError("No recognized web search tool")
                properties = tool.inputSchema.get("properties", {})
                query = "贵阳 花溪 旅游 景点"
                args = {}
                for key in properties:
                    if key in {"query", "q", "search_query"}: args[key] = query
                    elif key == "objective": args[key] = "查找贵阳花溪旅游景点的公开网页与来源"
                    elif key == "search_queries": args[key] = [query]
                    elif key in {"max_results", "limit", "count"}: args[key] = 3
                    elif key == "engines": args[key] = ["bing"]
                response = await session.call_tool(tool.name, args)
                payload = response.model_dump(mode="json", exclude_none=True)
                row.update(tool=tool.name, arguments=args, response=payload, ok=not response.isError)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError): raise
        row["error"] = str(exc)
        if getattr(exc, "exceptions", None): row["error"] += " " + repr(exc.exceptions)
    print(json.dumps({k: v for k, v in row.items() if k not in {"response", "tools"}}, ensure_ascii=False), flush=True)
    return row


async def main():
    rows = []
    for name, url in TARGETS.items():
        try:
            rows.append(await asyncio.wait_for(probe(name, url), 65))
        except asyncio.TimeoutError:
            rows.append({"id": name, "url": url, "ok": False, "error": "overall timeout"})
    path = Path(__file__).resolve().parents[1] / "outputs" / "web_mcp_probe.json"
    path.write_text(json.dumps({"tested_at": datetime.now(timezone.utc).isoformat(), "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__": asyncio.run(main())
