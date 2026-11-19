"""真实 MCP 适配器验收：中文旅游、中文网络用语、英文科技搜索。无模型。"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search_sources import MCPWebSearchClient, FREE_IDS

async def main():
    client=MCPWebSearchClient()
    results=[]
    for provider in sorted(FREE_IDS):
        for query in ['贵阳 花溪 旅游 景点','大肥鱼 网络用语 含义','coding agents benchmark SWE bench']:
            try:
                rows=await asyncio.wait_for(client.search(provider,query,3),60)
                assert rows and all(r['source'].startswith(('http://','https://')) and len(r['content'])>40 for r in rows)
                row={'id':provider,'query':query,'ok':True,'result_count':len(rows),'titles':[r['title'] for r in rows],'urls':[r['source'] for r in rows]}
            except Exception as exc: row={'id':provider,'query':query,'ok':False,'error':str(exc)}
            results.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
    output=Path(__file__).resolve().parents[1]/'outputs'/'web_mcp_adapter_probe.json'
    output.write_text(json.dumps({'tested_at':datetime.now(timezone.utc).isoformat(),'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    await client.aclose()
    return 0 if all(r['ok'] for r in results) else 1
if __name__=='__main__':sys.exit(asyncio.run(main()))
