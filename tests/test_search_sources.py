import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import httpx
from starlette.requests import Request
import pipeline,server,search_sources as sources
from schemas import ResearchPlan,SubQuestion,VerificationReport

class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_sources_rejected_and_dangerous_url_removed(self):
        for value in [[],['gdelt'],['github'],['crossref'],['openlibrary'],['stackexchange'],['hackernews']]:
            with self.assertRaises(ValueError):sources.validate_sources(value)
        data={'results':[{'title':'bad','url':'javascript:alert(1)','excerpts':['test']},{'title':'ok','url':'https://example.com','excerpts':['actual excerpt']}]}
        self.assertEqual(len(sources.parse_results('parallel',data)),1)
        with self.assertRaises(sources.SourceError):sources.parse_results('you',{})

    async def test_recorded_actual_mcp_payloads_parse(self):
        file=Path(__file__).resolve().parents[1]/'outputs/web_mcp_probe.json'
        if not file.exists():self.skipTest('Live probe artifact not present')
        rows=json.loads(file.read_text(encoding='utf-8'))['results']
        for row in rows:
            if row['id'] not in sources.FREE_IDS:continue
            data=json.loads(row['response']['content'][0]['text'])
            parsed=sources.parse_results(row['id'],data,3)
            self.assertTrue(parsed)
            self.assertTrue(any('花溪' in r['title'] for r in parsed))

    async def test_pipeline_only_uses_selected_mcp_and_keeps_partial_success(self):
        plan=ResearchPlan(goal='test',subquestions=[SubQuestion(question='q1',search_queries=['query'])],report_types=['report'])
        order,calls,events=[],[],[]
        async def emit(ev):events.append(ev)
        async def confirm(_):order.append('confirmed');return ''
        async def select(payload):order.append('selected');self.assertFalse(calls);return ['parallel','you']
        async def fake_search(_self,provider,query):
            calls.append(provider)
            if provider=='you':raise sources.SourceError('temporary failure')
            return [{'title':'web','source':'https://example.com','content':'real excerpt','provider':provider}]
        async def run(_agent,_prompt,schema,**kwargs):
            if schema is ResearchPlan:return plan,'',{}
            if schema is VerificationReport:return VerificationReport(verdict='pass',summary='ok'),'',{}
            raise RuntimeError('stop before model analysis')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(pipeline,'build_agent',return_value=object()),patch.object(pipeline,'make_local_tools',return_value=[]),patch.object(pipeline,'run_json',side_effect=run),patch.object(sources.MCPWebSearchClient,'search',new=fake_search),patch.object(pipeline,'websearch_mcp_session',side_effect=AssertionError('paid fallback forbidden')),patch.object(pipeline.config,'PLAN_DIR',root),patch.object(pipeline.config,'EVIDENCE_DIR',root),patch.object(pipeline.config,'CHECKPOINT_DIR',root):
                with self.assertRaisesRegex(RuntimeError,'stop before model analysis'):await pipeline.run_pipeline('test','test',emit,confirm,run_id='unit',select_sources=select)
                checkpoint=json.loads((root/'ckpt_unit.json').read_text(encoding='utf-8'))
        self.assertEqual(order,['confirmed','selected']);self.assertEqual(set(calls),{'parallel','you'})
        self.assertEqual(len(checkpoint['evidence']),1)
        started=[e['call_id'] for e in events if e['type']=='tool_call']
        returned=[e['call_id'] for e in events if e['type'] in {'tool_result','tool_error'}]
        self.assertEqual(set(started),set(returned))

class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.persist=patch.object(server,'_persist_events',new=AsyncMock());self.persist.start()
        self.state=server.RunState('test','test');server.runs[self.state.id]=self.state
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')
        emit=await server._emit_for(self.state);choose=await server._sources_for(self.state,emit)
        self.waiter=asyncio.create_task(choose({'options':sources.source_options(),'selected':[]}));await asyncio.sleep(0)
    async def asyncTearDown(self):
        self.waiter.cancel();await asyncio.gather(self.waiter,return_exceptions=True)
        server.runs.pop(self.state.id,None);await self.client.aclose();self.persist.stop()
    async def test_choice_validation_snapshot_and_resume_guard(self):
        url=f'/api/runs/{self.state.id}';d=(await self.client.get(url)).json();rid=d['pending_sources']['request_id']
        self.assertIn('timeline',d)
        for values,request_id,status in [([],rid,400),(['gdelt'],rid,400),(['parallel'],'stale',409)]:
            r=await self.client.post(url+'/sources',json={'sources':values,'request_id':request_id});self.assertEqual(r.status_code,status)
        self.assertEqual((await self.client.post(url+'/resume')).status_code,409)
        self.assertEqual((await self.client.post(url+'/sources',json={'sources':['parallel'],'request_id':rid})).status_code,200)
        self.assertEqual(await self.waiter,['parallel'])
    async def test_cancel_and_immediate_events(self):
        request=Request({'type':'http','headers':[]});response=await server.run_events(self.state.id,request,after=-1)
        self.assertIn('sources_required',await asyncio.wait_for(response.body_iterator.__anext__(),.5))
        emit=await server._emit_for(self.state);await emit({'type':'second'})
        self.assertIn('second',await asyncio.wait_for(response.body_iterator.__anext__(),.5));await response.body_iterator.aclose()
        self.state.task_obj=self.waiter
        self.assertEqual((await self.client.post(f'/api/runs/{self.state.id}/cancel')).status_code,200)
        await asyncio.gather(self.waiter,return_exceptions=True);self.assertEqual(self.state.status,'cancelled')

if __name__=='__main__':unittest.main()
