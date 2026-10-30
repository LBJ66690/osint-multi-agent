"""独立报告问答验收：模拟模型，不使用真实 Key 或调用云服务。"""
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
import server, report_chat, config, agents


class ReportChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(server,'runs',{}),patch.object(config,'OUTPUT_DIR',self.root),
                      patch.object(config,'REPORT_DIR',self.root),patch.object(config,'DASHSCOPE_API_KEY','test-chat-credential'),
                      patch.object(report_chat,'_inflight',set())]
        for p in self.patches:p.start()
        self.state=server.RunState('原始研究','qwen3.8-flash');self.state.status='done'
        self.state.reports=[{'type':'报告甲','markdown':'# 报告甲\n完整的原始结论与来源。\n[来源](https://example.com/a)'},
                            {'type':'报告乙','markdown':'# 报告乙\n这是另一份报告，不能混入报告甲。'}]
        server.runs[self.state.id]=self.state
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')
        self.model=AsyncMock(return_value=('根据报告，结论需要保留局限。',{'prompt':20,'completion':10,'total':30}))
        self.model_patch=patch.object(report_chat,'generate_answer',new=self.model);self.model_patch.start()
    async def asyncTearDown(self):
        await self.client.aclose();self.model_patch.stop()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def url(self):return f'/api/runs/{self.state.id}/report-chat'
    async def info(self,typ='报告甲'):
        return await self.client.get(self.url(),params={'report_type':typ})
    async def body(self,typ='报告甲',**overrides):
        data=(await self.info(typ)).json()
        return dict(report_type=typ,report_version=data['report_version'],model_id='qwen3.8-flash',
                    question='这份报告最重要的结论是什么？',request_id='question-001')|overrides

    async def test_selected_report_and_model_sent_without_research_side_effects(self):
        reports=json.dumps(self.state.reports,ensure_ascii=False)
        with patch('pipeline.run_pipeline',side_effect=AssertionError('must not run research')):
            r=await self.client.post(self.url(),json=await self.body(model_id='kimi-k3',report='伪造报告内容'))
        self.assertEqual(r.status_code,200)
        model,prompt=self.model.call_args.args;packet=json.loads(prompt)
        self.assertEqual(model,'kimi-k3');self.assertEqual(packet['report'],self.state.reports[0]['markdown'])
        self.assertNotIn('报告乙',prompt);self.assertNotIn('伪造报告内容',prompt)
        self.assertEqual(self.state.status,'done');self.assertEqual(self.state.events,[]);self.assertIsNone(self.state.task_obj)
        self.assertEqual(json.dumps(self.state.reports,ensure_ascii=False),reports)
        self.assertEqual((await self.info()).json()['turns'][0]['model_id'],'kimi-k3')
        self.assertTrue((self.root/'report_chats').is_dir())
        self.assertFalse((self.root/'checkpoints').exists())

    async def test_multiple_turns_include_latest_three_and_models_can_change(self):
        for i in range(5):
            body=await self.body(question=f'第{i}个追问',request_id=f'question-{i:03}',model_id='kimi-k3' if i%2 else 'qwen3.8-flash')
            self.assertEqual((await self.client.post(self.url(),json=body)).status_code,200)
        packet=json.loads(self.model.call_args.args[1])
        self.assertEqual(len(packet['conversation']),3);self.assertEqual(packet['conversation'][0]['question'],'第1个追问')
        self.assertEqual(packet['question'],'第4个追问')
        self.assertEqual((await self.info('报告乙')).json()['turns'],[])

    async def test_retry_same_request_is_idempotent_but_different_question_rejected(self):
        body=await self.body()
        self.assertEqual((await self.client.post(self.url(),json=body)).status_code,200)
        repeated=await self.client.post(self.url(),json=body)
        self.assertTrue(repeated.json()['reused']);self.assertEqual(self.model.call_count,1)
        r=await self.client.post(self.url(),json={**body,'question':'不同问题'})
        self.assertEqual(r.status_code,409);self.assertEqual(self.model.call_count,1)

    async def test_parallel_double_send_is_rejected_without_second_model_call(self):
        started=asyncio.Event();finish=asyncio.Event()
        async def wait(*args):started.set();await finish.wait();return '回答',None
        self.model.side_effect=wait;body=await self.body()
        task=asyncio.create_task(self.client.post(self.url(),json=body))
        try:
            await asyncio.wait_for(started.wait(),2)
            self.assertEqual((await self.client.post(self.url(),json=body)).status_code,429)
        finally:finish.set()
        self.assertEqual((await task).status_code,200);self.assertEqual(self.model.call_count,1)

    async def test_timeout_failure_and_empty_response_preserve_report_and_allow_retry(self):
        for error in [asyncio.TimeoutError(),RuntimeError(config.DASHSCOPE_API_KEY)]:
            self.model.side_effect=error;body=await self.body()
            r=await self.client.post(self.url(),json=body)
            self.assertIn(r.status_code,[502,504]);self.assertNotIn(config.DASHSCOPE_API_KEY,r.text)
            self.assertEqual((await self.info()).json()['turns'],[]);self.assertEqual(report_chat._inflight,set())
        self.model.side_effect=None;self.model.return_value=('',None)
        self.assertEqual((await self.client.post(self.url(),json=await self.body())).status_code,502)
        self.model.return_value=('重新回答成功',None)
        self.assertEqual((await self.client.post(self.url(),json=await self.body())).status_code,200)

    async def test_version_change_isolated_and_old_request_cannot_answer_new_report(self):
        body=await self.body();await self.client.post(self.url(),json=body)
        self.state.reports[0]['markdown']+='\n报告已修订。'
        self.assertEqual((await self.info()).json()['turns'],[])
        self.assertEqual((await self.client.post(self.url(),json=body)).status_code,409)
        self.assertEqual(self.model.call_count,1)

    async def test_invalid_states_models_and_question_are_rejected(self):
        body=await self.body()
        for status in ['running','error','cancelled']:
            self.state.status=status
            self.assertEqual((await self.info()).status_code,409)
            self.assertEqual((await self.client.post(self.url(),json=body)).status_code,409)
        self.state.status='done'
        for changes,code in [({'model_id':'unknown'},400),({'question':'   '},422),({'question':'x'*4001},422),({'request_id':'../bad'},422),({'report_type':'其他'},404)]:
            self.assertEqual((await self.client.post(self.url(),json=body|changes)).status_code,code)
        with patch.object(server,'model_unavailable',return_value='quota exhausted'):
            self.assertEqual((await self.client.post(self.url(),json=body)).status_code,400)
        with patch.object(config,'DASHSCOPE_API_KEY',''):
            self.assertEqual((await self.client.post(self.url(),json=body)).status_code,503)
        self.model.assert_not_called()

    async def test_unknown_run_and_report_are_not_readable(self):
        self.assertEqual((await self.client.get('/api/runs/missing/report-chat',params={'report_type':'报告甲'})).status_code,404)
        body=await self.body()
        self.assertEqual((await self.client.post('/api/runs/missing/report-chat',json=body)).status_code,404)
        self.assertEqual((await self.info('../../other')).status_code,404)

    async def test_report_too_long_or_empty_rejected_without_truncation(self):
        self.state.reports[0]['markdown']='x'*40001
        self.assertEqual((await self.info()).status_code,413)
        self.state.reports[0]['markdown']=' '
        self.assertEqual((await self.info()).status_code,409);self.model.assert_not_called()

    async def test_conversation_survives_memory_reset_and_corrupt_file_preserved(self):
        body=await self.body();await self.client.post(self.url(),json=body)
        report_chat._inflight.clear()
        self.assertEqual(len((await self.info()).json()['turns']),1)
        path=next((self.root/'report_chats').glob('*.json'));path.write_text('{broken',encoding='utf-8')
        self.assertEqual((await self.info()).status_code,409);self.assertEqual(path.read_text(),'{broken')

    async def test_save_failure_does_not_modify_original_report_or_keep_lock(self):
        before=self.state.reports[0]['markdown']
        with patch.object(report_chat,'_save',side_effect=OSError('read only')):
            response=await self.client.post(self.url(),json=await self.body())
        self.assertEqual(response.status_code,503);self.assertIn('模型已作答',response.json()['detail'])
        self.assertEqual((await self.info()).json()['turns'],[]);self.assertEqual(report_chat._inflight,set())
        self.assertEqual(self.state.reports[0]['markdown'],before)

    async def test_secret_redacted_from_reply_question_and_history(self):
        self.model.return_value=('回答 '+config.DASHSCOPE_API_KEY,None)
        r=await self.client.post(self.url(),json=await self.body(question='问题 '+config.DASHSCOPE_API_KEY))
        self.assertEqual(r.status_code,200);self.assertNotIn(config.DASHSCOPE_API_KEY,r.text)
        for path in (self.root/'report_chats').glob('*.json'):
            self.assertNotIn(config.DASHSCOPE_API_KEY,path.read_text(encoding='utf-8'))

    async def test_50_turn_limit_and_global_busy_limit(self):
        body=await self.body();data,path,_=report_chat._load(self.state.id,'报告甲',self.state.reports[0]['markdown'])
        data['turns']=[dict(id=f'old-{i}',question='问题',answer='回答',model_id='qwen3.8-flash') for i in range(50)]
        report_chat._save(path,data)
        self.assertEqual((await self.client.post(self.url(),json=body)).status_code,409)
        self.model.assert_not_called()
        report_chat._inflight.update(str(i) for i in range(4))
        self.assertEqual((await self.client.post(self.url(),json=body)).status_code,429)

    async def test_cancellation_releases_inflight_without_persisting_partial_answer(self):
        started=asyncio.Event()
        async def wait(*args):started.set();await asyncio.sleep(30)
        self.model.side_effect=wait;body=await self.body()
        task=asyncio.create_task(self.client.post(self.url(),json=body))
        await asyncio.wait_for(started.wait(),2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(report_chat._inflight,set());self.assertEqual((await self.info()).json()['turns'],[])

    async def test_cancel_during_atomic_save_keeps_answer_and_idempotency(self):
        started=threading.Event();finish=threading.Event();original=report_chat._save
        def save(path,data):
            started.set();finish.wait(3);original(path,data)
        body=await self.body()
        with patch.object(report_chat,'_save',side_effect=save):
            task=asyncio.create_task(self.client.post(self.url(),json=body))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,2));task.cancel()
                await asyncio.sleep(.01);self.assertTrue(report_chat._inflight)
            finally:finish.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(len((await self.info()).json()['turns']),1)
        self.assertTrue((await self.client.post(self.url(),json=body)).json()['reused'])
        self.assertEqual(self.model.call_count,1)


class ModelAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_adapter_has_no_search_tools_and_closes_client(self):
        close=AsyncMock();agent=SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(text='回答',usage_details=None)),
            client=SimpleNamespace(client=SimpleNamespace(close=close)))
        with patch.object(agents,'build_agent',return_value=agent) as build:
            answer,usage=await report_chat.generate_answer('kimi-k3','报告和问题')
        self.assertEqual(answer,'回答');self.assertIsNone(usage);close.assert_awaited_once()
        self.assertEqual(build.call_args.args[0],'kimi-k3');self.assertNotIn('tools',build.call_args.kwargs)

    async def test_model_adapter_timeout_closes_client(self):
        async def slow(*args):await asyncio.sleep(10)
        close=AsyncMock();agent=SimpleNamespace(run=slow,client=SimpleNamespace(client=SimpleNamespace(close=close)))
        with patch.object(agents,'build_agent',return_value=agent),patch.object(report_chat,'TIMEOUT_SECONDS',.01):
            with self.assertRaises(asyncio.TimeoutError):await report_chat.generate_answer('qwen3.8-flash','测试')
        close.assert_awaited_once()

    async def test_long_history_is_bounded_without_truncating_report(self):
        report='报告正文'*7000
        prompt,count=report_chat.build_prompt('报告',report,[dict(question='问题'*2000,answer='回答'*7000) for _ in range(5)],'当前问题')
        self.assertLessEqual(len(prompt),64000);self.assertEqual(json.loads(prompt)['report'],report)
        self.assertLessEqual(count,3)
