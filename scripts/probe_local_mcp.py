"""保留的本地 stdio MCP：实际子进程握手、工具、资源和提示模板验收。"""
import asyncio,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    started=time.monotonic()
    try:
        async def request():
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B',str(config.ROOT/'mcp_local_server.py')])) as (reader,writer):
                async with ClientSession(reader,writer) as session:
                    await session.initialize();tools=await session.list_tools()
                    template=await session.call_tool('_get_report_template',{'report_type':'简明解答'})
                    briefing=await session.call_tool('_get_local_briefing',{'topic':'贵阳花溪区'})
                    resource=await session.read_resource('osint://report-types')
                    prompt=await session.get_prompt('report_outline',{'report_type':'研究报告','topic':'验收'})
                    assert not template.isError and '先直接回答' in template.content[0].text
                    assert not briefing.isError and '花溪' in briefing.content[0].text
                    assert '研究报告' in resource.contents[0].text and prompt.messages
                    return {'ok':True,'tools':[t.name for t in tools.tools],'resource':True,'prompt':True}
        result=await asyncio.wait_for(request(),20)
    except Exception as exc:result={'ok':False,'error':str(exc)[:400]}
    result['seconds']=round(time.monotonic()-started,2)
    (config.OUTPUT_DIR/'acceptance_local_mcp.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['ok'] else 1
if __name__=='__main__':sys.exit(asyncio.run(main()))
