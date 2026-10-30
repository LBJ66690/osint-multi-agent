"""功能验收：API 边界、报告交付、历史恢复、SSE 和资源上限。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
import httpx
from starlette.requests import Request
import server,pipeline
from schemas import VerificationReport

class ApiAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name)
        self.patches=[patch.object(server,'runs',{}),patch.object(server.config,'REPORT_DIR',self.root),patch.object(server.config,'CHECKPOINT_DIR',self.root),patch.object(server.config,'EVENTS_DIR',self.root)]
        for p in self.patches:p.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')
        self.state=server.RunState('验收','qwen3.8-flash');server.runs[self.state.id]=self.state
    async def asyncTearDown(self):
        for state in list(server.runs.values()):
            if state.task_obj and not state.task_obj.done():state.task_obj.cancel();await asyncio.gather(state.task_obj,return_exceptions=True)
        await self.client.aclose()
        for p in reversed(self.patches):p.stop()
        self.folder.cleanup()
    async def test_cancel_completed_run_preserves_completed_state(self):
        self.state.status='done'
        response=await self.client.post(f'/api/runs/{self.state.id}/cancel')
        self.assertEqual(response.status_code,409);self.assertEqual(self.state.status,'done')
    async def test_invalid_gate_decision_is_rejected(self):
        self.state.status='awaiting_gate';self.state.pending_gate={'stage':'reasoning'}
        response=await self.client.post(f'/api/runs/{self.state.id}/gate',json={'decision':'redoo'})
        self.assertEqual(response.status_code,422)
    async def test_malformed_create_requests_and_unknown_routes(self):
        for body,expected in [({'task':''},400),({'task':'x','model_id':'unknown'},400),({'task':'x','execution_mode':'bad'},422),({'task':'x','role_models':{'analyst':'bad'}},400),({},422)]:
            with self.subTest(body=body):self.assertEqual((await self.client.post('/api/runs',json=body)).status_code,expected)
        for suffix in ['', '/confirm','/sources','/gate','/resume','/cancel','/report.md']:
            response=await (self.client.get('/api/runs/missing'+suffix) if suffix in {'','/report.md'} else self.client.post('/api/runs/missing'+suffix,json={'feedback':'','request_id':'x','sources':['parallel'],'decision':'continue'}))
            self.assertEqual(response.status_code,404)
    async def test_report_event_available_before_final_completion(self):
        path=self.root/f'report_{self.state.id}.md';path.write_text('# 已生成正文',encoding='utf-8')
        emit=await server._emit_for(self.state)
        await emit({'type':'report','report_type':'已生成报告','path':str(path),'markdown':'# 已生成正文'})
        response=await self.client.get(f'/api/runs/{self.state.id}/report.md')
        self.assertEqual(response.status_code,200);self.assertIn('已生成正文',response.text)
    async def test_download_multiple_reports_and_chinese_filename(self):
        self.state.reports=[{'type':'第一份','markdown':'# A'},{'type':'第二份','markdown':'# B'}]
        response=await self.client.get(f'/api/runs/{self.state.id}/report.md')
        self.assertEqual(response.status_code,200);self.assertIn('# A',response.text);self.assertIn('# B',response.text)
        self.assertIn("filename*=UTF-8''",response.headers['content-disposition'])
    async def test_unregistered_report_file_not_readable(self):
        path=self.root/f'foreign_{self.state.id}.md';path.write_text('foreign',encoding='utf-8')
        response=await self.client.get(f'/api/reports/{self.state.id}/{path.name}')
        self.assertEqual(response.status_code,404)
        self.state.reports=[{'type':'own','path':str(path),'markdown':'foreign'}]
        self.assertEqual((await self.client.get(f'/api/reports/{self.state.id}/{path.name}')).status_code,200)
    async def test_wrong_state_confirm_gate_and_sources_rejected(self):
        for suffix,body in [('confirm',{'feedback':''}),('gate',{'decision':'continue'}),('sources',{'request_id':'x','sources':['parallel']})]:
            self.assertEqual((await self.client.post(f'/api/runs/{self.state.id}/{suffix}',json=body)).status_code,409)
    async def test_lru_does_not_orphan_active_tasks(self):
        with patch.object(server,'MAX_RUNS',1):
            self.state.status='running'
            response=await self.client.post('/api/runs',json={'task':'another'})
            self.assertEqual(response.status_code,429);self.assertIn(self.state.id,server.runs)
    async def test_sse_outdated_cursor_receives_next_event(self):
        self.state.events=[{'type':'old'}]
        response=await server.run_events(self.state.id,Request({'type':'http','headers':[(b'last-event-id',b'999')]}))
        next_event=asyncio.create_task(response.body_iterator.__anext__());await asyncio.sleep(.01)
        emit=await server._emit_for(self.state);await emit({'type':'new'})
        try:self.assertIn('"new"',await asyncio.wait_for(next_event,.2))
        finally:await response.body_iterator.aclose()
    async def test_sse_terminal_stream_and_valid_cursor(self):
        self.state.status='done';self.state.events=[{'type':'one'},{'type':'two'}]
        response=await server.run_events(self.state.id,Request({'type':'http','headers':[]}),after=0)
        self.assertIn('"two"',await response.body_iterator.__anext__());self.assertIn('stream_end',await response.body_iterator.__anext__())
        await response.body_iterator.aclose()
    async def test_unknown_verification_verdict_rejected(self):
        with self.assertRaises(ValueError):VerificationReport(verdict='maybe',summary='wrong')

    async def test_cli_can_resume_and_rejects_missing_checkpoint(self):
        import main
        checkpoint={'run_id':'cli','task':'saved task','completed_stages':[]}
        with patch.object(main.config,'check_key'),patch.object(main.pipeline,'load_checkpoint',return_value=checkpoint),patch.object(main.pipeline,'run_pipeline',new=AsyncMock(return_value={'reports':[]})) as run:
            self.assertEqual(await main.main(['--resume','cli']),0)
            self.assertTrue(run.call_args.kwargs['resume']);self.assertEqual(run.call_args.kwargs['run_id'],'cli')
        with patch.object(main.config,'check_key'),patch.object(main.pipeline,'load_checkpoint',return_value=None):
            self.assertEqual(await main.main(['--resume','missing']),1)

    async def test_unavailable_quota_model_rejected_without_changing_billing(self):
        import hashlib,time
        data={'tested_at':time.time(),'account_fingerprint':hashlib.sha256(server.config.DASHSCOPE_API_KEY.encode()).hexdigest(),'models':[{'id':'qwen3.7-flash','ok':False,'error':'403 Free quota exhausted'}]}
        with patch.object(server.config,'OUTPUT_DIR',self.root):
            (self.root/'acceptance_models.json').write_text(json.dumps(data),encoding='utf-8')
            response=await self.client.post('/api/runs',json={'task':'test','role_models':{'planner':'qwen3.7-flash'}})
            self.assertEqual(response.status_code,400)
            listed=(await self.client.get('/api/models')).json()['models']
            self.assertFalse(next(m for m in listed if m['id']=='qwen3.7-flash')['available'])
    async def test_missing_and_corrupt_checkpoint_preserved(self):
        self.assertIsNone(pipeline.load_checkpoint('missing'))
        path=self.root/'ckpt_broken.json';path.write_text('{broken',encoding='utf-8')
        self.assertIsNone(pipeline.load_checkpoint('broken'));self.assertEqual(path.read_text(encoding='utf-8'),'{broken')
    async def test_static_resources_available(self):
        for path in ['/','/static/app.js','/static/style.css','/static/visual-effects.js','/static/visual-effects.css','/api/models','/api/search-sources','/api/health']:
            with self.subTest(path=path):self.assertEqual((await self.client.get(path)).status_code,200)

if __name__=='__main__':unittest.main()
