"""真实 FastAPI/SSE 与浏览器联调；仅模型和外部搜索替换为固定数据。"""
import asyncio,sys,logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn,config,pipeline,search_sources,server,report_chat,json
from schemas import ResearchPlan,SubQuestion,Knowledge,ViewPoint,Analysis,Finding,VerificationReport,VerificationIssue
root=config.OUTPUT_DIR/'acceptance_web';root.mkdir(exist_ok=True)
for handler in config.logger.handlers[:]:config.logger.removeHandler(handler);handler.close()
config.logger.addHandler(logging.FileHandler(root/'application.log',encoding='utf-8'))
for name in ['CHECKPOINT_DIR','PLAN_DIR','EVIDENCE_DIR','ANALYSIS_DIR','REPORT_DIR','EVENTS_DIR']:setattr(config,name,root)
async def model(agent,prompt,schema,**kwargs):
    await asyncio.sleep(.08)
    if schema is ResearchPlan:value=ResearchPlan(goal='浏览器验收研究（固定数据）',subquestions=[SubQuestion(question='花溪公园特点',search_queries=['花溪'])],report_types=['验收报告'])
    elif schema is Knowledge:value=Knowledge(viewpoints=[ViewPoint(topic='花溪',sentiment='中性',summary='固定数据说明')])
    elif schema is Analysis:value=Analysis(findings=[Finding(claim='公园适合散步（固定数据）',evidence=['固定片段'],sources=['https://example.com/huaxi'])])
    else:
        fail='报告正文' in prompt and '完整浏览器联调验收' in prompt and not getattr(agent,'failed',False)
        if fail:agent.failed=True
        value=VerificationReport(verdict='fail' if fail else 'pass',summary='验收：测试重写关卡' if fail else '固定数据核查通过',issues=[VerificationIssue(claim='补充来源',verdict='存疑',explanation='固定测试问题',severity='中')] if fail else [])
    return value,'',{'total':100}
async def search(_client,provider,query,*args,**kwargs):
    await asyncio.sleep(.15)
    return [{'title':'花溪公园固定来源','source':'https://example.com/huaxi','content':'固定数据：公园适合散步。','provider':provider}]
class Stream:
    def __aiter__(self):return self.updates()
    async def updates(self):
        for text in ['# 浏览器联调验收报告\n\n','本报告为固定测试数据。\n','[E1](https://example.com/huaxi)']:
            await asyncio.sleep(.08);yield SimpleNamespace(text=text)
    async def get_final_response(self):return SimpleNamespace(text='# 浏览器联调验收报告\n\n本报告为固定测试数据。\n[E1](https://example.com/huaxi)')
class Reporter:
    def run(self,prompt,stream=False):return Stream()
def build(_m,_i,name,**kwargs):return Reporter() if name=='报告官' else SimpleNamespace(name=name)
async def chat_answer(model_id,prompt):
    packet=json.loads(prompt)
    assert '浏览器联调验收报告' in packet['report']
    assert packet['question']
    await asyncio.sleep(.1)
    return f"固定测试回答（模型 {model_id}）：这份报告是浏览器联调验收的固定数据，不是实时研究。",{'prompt':40,'completion':20,'total':60}
with patch.object(pipeline,'build_agent',side_effect=build),patch.object(pipeline,'run_json',side_effect=model),patch.object(search_sources.MCPWebSearchClient,'search',new=search),patch.object(config,'DASHSCOPE_API_KEY','fixture-chat-credential'),patch.object(report_chat,'generate_answer',side_effect=chat_answer):
    uvicorn.run(server.app,host='127.0.0.1',port=8890,log_level='warning')
