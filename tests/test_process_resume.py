"""实际终止独立测试进程，再启动新进程恢复；模型和搜索用固定数据。"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

CHILD = r'''
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pipeline,search_sources
from schemas import ResearchPlan,SubQuestion,Knowledge,ViewPoint,Analysis,Finding,VerificationReport
root=Path(sys.argv[1]);second=sys.argv[2]=='second'
def mark(text):
    with (root/'calls.txt').open('a',encoding='utf-8') as file:file.write(text+'\n')
async def model(agent,prompt,schema,**kwargs):
    mark(schema.__name__)
    if schema is Analysis and not second:
        (root/'ready').write_text('reasoning',encoding='utf-8')
        await asyncio.sleep(120)
    values={ResearchPlan:lambda:ResearchPlan(goal='test',subquestions=[SubQuestion(question='q',search_queries=['q'])],report_types=['report']),
        Knowledge:lambda:Knowledge(viewpoints=[ViewPoint(topic='t',sentiment='中性',summary='test')]),
        Analysis:lambda:Analysis(findings=[Finding(claim='test',evidence=['text'],sources=['https://example.com'])]),
        VerificationReport:lambda:VerificationReport(verdict='pass',summary='ok')}
    return values[schema](),'',{}
async def search(*args,**kwargs):
    mark('search');return [{'title':'test','content':'text','source':'https://example.com','provider':'parallel'}]
async def report(*args,**kwargs):mark('report');return '# saved report'
async def noop(*args):pass
async def confirm(*args):return ''
async def select(*args):return ['parallel']
async def main():
    for name in ['CHECKPOINT_DIR','PLAN_DIR','EVIDENCE_DIR','REPORT_DIR','ANALYSIS_DIR']:setattr(pipeline.config,name,root)
    with patch.object(pipeline,'build_agent',return_value=SimpleNamespace()),patch.object(pipeline,'run_json',side_effect=model),patch.object(pipeline,'generate_report',side_effect=report),patch.object(search_sources.MCPWebSearchClient,'search',new=search):
        await pipeline.run_pipeline('test','qwen3.8-flash',noop,confirm,run_id='hardstop',execution_mode='standard',select_sources=select,resume=second)
asyncio.run(main())
'''

class ProcessResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_killed_process_can_resume_without_redoing_completed_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=await asyncio.create_subprocess_exec(sys.executable,'-B','-c',CHILD,folder,'first',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                async def ready():
                    while not (root/'ready').exists():
                        if first.returncode is not None:raise AssertionError(await first.communicate())
                        await asyncio.sleep(.02)
                await asyncio.wait_for(ready(),12)
                first.kill();await first.communicate()
                before=json.loads((root/'ckpt_hardstop.json').read_text(encoding='utf-8'))
                self.assertEqual(before['completed_stages'],['planning','collecting','analyzing'])
                second=await asyncio.create_subprocess_exec(sys.executable,'-B','-c',CHILD,folder,'second',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                try:
                    stdout,stderr=await asyncio.wait_for(second.communicate(),12)
                    self.assertEqual(second.returncode,0,stderr.decode(errors='replace'))
                finally:
                    if second.returncode is None:second.kill();await second.communicate()
                after=json.loads((root/'ckpt_hardstop.json').read_text(encoding='utf-8'))
                calls=(root/'calls.txt').read_text(encoding='utf-8').splitlines()
                self.assertEqual(after['status'],'done')
                for kind in ['ResearchPlan','Knowledge','search','report']:self.assertEqual(calls.count(kind),1)
                self.assertEqual(calls.count('Analysis'),2)
                self.assertEqual(before['operations']['analyzing:produce:0'],after['operations']['analyzing:produce:0'])
            finally:
                if first.returncode is None:first.kill();await first.communicate()
