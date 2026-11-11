"""真实 API 的用量快照/重启恢复；不向外部发请求。"""
import json
import unittest
from unittest.mock import patch

import httpx
import pipeline
import server
from resource_usage import consume_event, snapshot
import test_execution as fixtures


class ResourceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f=fixtures.Fixture();await self.f.asyncSetUp()
        self.f.patches.enter_context(patch.object(server,'runs',{}))
        self.f.patches.enter_context(patch.object(server.config,'EVENTS_DIR',self.f.root))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://local.test')

    async def asyncTearDown(self):
        await self.client.aclose();await self.f.asyncTearDown()

    async def test_live_snapshot_and_restart_preserve_unknown_usage_and_failure(self):
        state=server.RunState('固定测试任务','qwen3.8-flash');state.status='error'
        server.runs[state.id]=state
        raw={}
        consume_event(raw,{'type':'model_request','request_id':'fixture-1','role':'reasoner','operation':'reasoning:produce:0'})
        consume_event(raw,{'type':'model_error','request_id':'fixture-1','role':'reasoner','error_type':'TimeoutError'})
        expected=snapshot(raw)
        emit=await server._emit_for(state)
        await emit({'type':'resource_usage','stats':expected})
        response=await self.client.get('/api/runs/'+state.id)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['summary']['resource_usage'],expected)
        await pipeline.save_checkpoint(state.id,{'run_id':state.id,'task':state.task,'model_id':state.model_id,
             'schema_version':2,'status':'error','completed_stages':[],'operations':{},'resource_usage':raw})
        server.runs.clear();server._restore_history_from_disk()
        restored=(await self.client.get('/api/runs/'+state.id)).json()
        self.assertEqual(restored['summary']['resource_usage'],expected)
        self.assertIsNone(restored['summary']['resource_usage']['total'])
        self.assertEqual(restored['summary']['resource_usage']['roles']['reasoner']['model_failures'],1)
        self.assertEqual(restored['resume_info']['next_stage'],'planning')

    async def test_old_history_has_no_fabricated_zero_usage(self):
        await pipeline.save_checkpoint('oldfixture',{'run_id':'oldfixture','task':'旧任务',
            'model_id':'qwen3.8-flash','schema_version':2,'status':'error','completed_stages':[],'operations':{}})
        server._restore_history_from_disk()
        detail=(await self.client.get('/api/runs/oldfixture')).json()
        self.assertIsNone(detail['summary']['resource_usage'])


if __name__=='__main__':unittest.main()
