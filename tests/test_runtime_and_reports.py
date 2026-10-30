import asyncio
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from report_generation import generate_report
from runtime_state import RunTimeline

class TimelineTests(unittest.TestCase):
    def test_cancel_during_user_wait_marks_stage_cancelled(self):
        t=RunTimeline()
        for event in [{'type':'stage','stage':'planning','status':'running'},{'type':'gate_check','verdict':'fail','interactive':True},{'type':'cancelled'}]:
            t.consume({'ts':'2026-10-30T00:00:00+00:00',**event})
        self.assertEqual(t.stages['planning']['status'],'cancelled')
    def test_waiting_retries_and_terminal_time(self):
        t=RunTimeline()
        def event(second,kind,**extra):t.consume({'ts':f'2026-10-29T00:00:{second:02}.000+00:00','type':kind,**extra})
        event(0,'stage',stage='planning',status='running');event(1,'stage',stage='planning',status='done')
        event(1,'confirm_required');event(4,'confirm_resolved');event(4,'sources_required');event(9,'sources_selected')
        event(9,'stage',stage='collecting',status='running');event(10,'stage',stage='collecting',status='running')
        event(15,'error');event(20,'resumed');event(20,'stage',stage='collecting',status='running');event(22,'cancelled')
        self.assertEqual(t.wait_total,8000);self.assertEqual(t.stages['planning']['elapsed_ms'],1000)
        self.assertEqual(t.stages['collecting']['elapsed_ms'],8000);self.assertIsNone(t.active)
        self.assertEqual(t.snapshot()['ended_ms']-t.snapshot()['started_ms'],22000)

class Stream:
    def __init__(self,error=None):self.error=error
    def __aiter__(self):return self.updates()
    async def updates(self):
        yield SimpleNamespace(text='partial text')
        if self.error:raise self.error
    async def get_final_response(self):return SimpleNamespace(text='complete text')

class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_chunked_read_retries_then_nonstream_without_duplicate(self):
        class Reporter:
            def __init__(self):self.modes=[]
            def run(self,prompt,stream=False):
                self.modes.append(stream)
                if stream:return Stream(ConnectionError('incomplete chunked read'))
                async def final():return SimpleNamespace(text='clean final report')
                return final()
        reporter=Reporter();events=[];drafts=[]
        async def emit(ev):events.append(ev)
        async def save(text):drafts.append(text)
        text=await generate_report(reporter,'prompt','report',emit,save,lambda _: {},backoff=0)
        self.assertEqual(text,'clean final report');self.assertEqual(reporter.modes,[True,False])
        self.assertEqual(drafts,['partial text'])
        versions=[e['version'] for e in events if e['type']=='report_start'];self.assertEqual(len(set(versions)),2)
    async def test_permanent_error_not_retried(self):
        class Reporter:
            def run(self,prompt,stream=False):return Stream(ValueError('401 invalid API key'))
        events=[]
        async def emit(ev):events.append(ev)
        async def save(_):pass
        with self.assertRaises(ValueError):await generate_report(Reporter(),'prompt','report',emit,save,lambda _:{},backoff=0)
        self.assertEqual(len([e for e in events if e['type']=='report_start']),1)
    async def test_success_no_retry(self):
        class Reporter:
            def run(self,prompt,stream=False):return Stream()
        async def nothing(_):pass
        result=await generate_report(Reporter(),'prompt','report',nothing,nothing,lambda _:{},backoff=0)
        self.assertEqual(result,'complete text')

    async def test_pipeline_resume_reuses_completed_report(self):
        import pipeline
        from schemas import VerificationReport
        class Reporter:
            calls=0
            def run(self,prompt,stream=False):self.calls+=1;return Stream()
        reporter=Reporter();events=[]
        async def emit(ev):events.append(ev)
        async def confirm(_):raise AssertionError('plan should be restored')
        async def run_json(*_,**kwargs):return VerificationReport(verdict='pass',summary='ok'),'',{}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);first=root/'first.md';first.write_text('Saved first report',encoding='utf-8')
            checkpoint={'run_id':'resume','task':'test','model_id':'test','completed_stages':['planning','collecting','analyzing','reasoning'],
                'plan':{'goal':'test','subquestions':[{'question':'q','search_queries':['q']}],'report_types':['first','second']},
                'evidence':[{'subquestion':'q','title':'source','content':'source text','source':'https://example.com'}],
                'knowledge':{'aliases':{},'viewpoints':[]},'analysis':{'findings':[]},'reports':[str(first)],'report_records':[{'type':'first','path':str(first)}]}
            (root/'ckpt_resume.json').write_text(json.dumps(checkpoint),encoding='utf-8')
            def build(_model,_instructions,name,**kwargs):return reporter if name=='报告官' else object()
            with patch.object(pipeline.config,'CHECKPOINT_DIR',root),patch.object(pipeline.config,'REPORT_DIR',root),patch.object(pipeline,'build_agent',side_effect=build),patch.object(pipeline,'make_local_tools',return_value=[]),patch.object(pipeline,'run_json',side_effect=run_json):
                summary=await pipeline.run_pipeline('test','test',emit,confirm,run_id='resume',resume=True)
            self.assertEqual(reporter.calls,1)
            self.assertEqual(len(summary['reports']),2)
            self.assertTrue(first.read_text(encoding='utf-8').startswith('Saved first report'))
            self.assertTrue(any(e['type']=='report' and e['report_type']=='first' for e in events))

if __name__=='__main__':unittest.main()
