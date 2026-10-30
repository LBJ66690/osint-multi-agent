"""百炼 MCP 握手及真实网页查询验收，不调用收集模型。"""
import asyncio,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
from agents import websearch_mcp_session,search_result_count
async def main():
    start=time.monotonic();events=[]
    async def emit(event):events.append(event)
    try:
        async def request():
            async with websearch_mcp_session(emitter=emit) as tool:
                result=await tool.call_tool('bailian_web_search',query='贵阳 花溪公园 旅游',count=3)
                return result
        await asyncio.wait_for(request(),60)
        returned=[event for event in events if event['type']=='tool_result']
        row={'ok':bool(returned) and (returned[0].get('result_count') or 0)>0,'count':returned[0].get('result_count') if returned else None}
    except Exception as exc:row={'ok':False,'error':str(exc)[:350]}
    row['seconds']=round(time.monotonic()-start,2)
    (config.OUTPUT_DIR/'acceptance_bailian.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(row,ensure_ascii=False),flush=True)
    return 0 if row['ok'] else 1
if __name__=='__main__':sys.exit(asyncio.run(main()))
