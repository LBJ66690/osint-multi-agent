import asyncio
import json
import tempfile
import time
import unittest
from collections import Counter
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pipeline
import config
import server
import models
import search_sources
from agents import run_json, make_chat_client
from execution_policy import choose_policy, POLICIES
from execution_runtime import WorkBudget, ExecutionTimeout
from evidence_context import evidence_context
from schemas import (ResearchPlan, SubQuestion, Knowledge, ViewPoint, Analysis, Finding,
                     QuickSynthesis, VerificationReport, VerificationIssue, EvidenceItem)


class Fixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = ExitStack()
        for name in ['CHECKPOINT_DIR','PLAN_DIR','EVIDENCE_DIR','ANALYSIS_DIR','REPORT_DIR']:
            self.patches.enter_context(patch.object(pipeline.config,name,self.root))
        self.patches.enter_context(patch.object(pipeline.config.logger,'exception'))
        self.events, self.calls, self.searches = [], Counter(), []
        self.report_types=['简明解答']
        self.fail_schema, self.fail_report, self.fail_check_number = None, False, None
        self.fail_decision, self.report_fail_once = False, False
        self.knowledge = Knowledge(viewpoints=[ViewPoint(topic='测试',sentiment='中性',summary='有来源的观点')])
        self.analysis = Analysis(findings=[Finding(claim='测试结论',evidence=['真实片段'],sources=['https://example.com/source'])])
        self.patches.enter_context(patch.object(pipeline,'build_agent',side_effect=lambda _m,_i,name,**kw:SimpleNamespace(name=name)))
        self.patches.enter_context(patch.object(pipeline,'run_json',side_effect=self.model))
        self.patches.enter_context(patch.object(pipeline,'generate_report',side_effect=self.report))
        async def search(_client,provider,query):
            self.searches.append((provider,query))
            return [{'title':'测试来源','source':'https://example.com/source','content':'真实片段，与用户问题有关。','provider':provider}]
        self.patches.enter_context(patch.object(search_sources.MCPWebSearchClient,'search',new=search))

    async def asyncTearDown(self):
        self.patches.close()
        self.temp.cleanup()

    async def emit(self,event):
        self.events.append(event)

    async def confirm(self,_):
        return ''

    async def sources(self,_):
        return ['parallel']

    async def model(self,agent,prompt,schema,**kwargs):
        self.calls[schema.__name__] += 1
        if schema is self.fail_schema:
            raise ConnectionError('injected model failure')
        if schema is ResearchPlan:
            value=ResearchPlan(goal='测试',subquestions=[SubQuestion(question='测试问题',search_queries=['测试'])],report_types=self.report_types)
        elif schema is Knowledge:
            value=self.knowledge
        elif schema is Analysis:
            value=self.analysis
        elif schema is QuickSynthesis:
            value=QuickSynthesis(knowledge=self.knowledge,analysis=self.analysis)
        else:
            if self.calls['VerificationReport'] == self.fail_check_number:
                raise ConnectionError('injected check failure')
            fail=self.report_fail_once and '报告正文' in prompt
            self.report_fail_once=False
            value=VerificationReport(verdict='fail' if fail else 'pass',summary='需要修订' if fail else '通过',
                issues=[VerificationIssue(claim='修订点',verdict='存疑',explanation='核对引用',severity='中')] if fail else [])
        return value,'',{'total':1}

    async def report(self,*args,**kwargs):
        self.calls['report']+=1
        if self.fail_report:
            raise ConnectionError('injected report failure')
        return '# 测试报告\n结论有依据。[来源](https://example.com/source)'

    async def run_task(self,resume=False,**kwargs):
        return await pipeline.run_pipeline('这是什么','qwen3.8-flash',self.emit,kwargs.pop('confirm',self.confirm),
            run_id='test',resume=resume,select_sources=self.sources,**kwargs)

    def checkpoint(self):
        return json.loads((self.root/'ckpt_test.json').read_text(encoding='utf-8'))

    async def test_malformed_analysis_resumes_without_repeating_search(self):
        import agents as model_agents
        fail=[True]
        async def model(agent,prompt,schema,**kwargs):
            if schema is Knowledge:
                bad='{"entities":[],"viewpoints":[{"topic":"测试"'
                replies=iter([bad,bad] if fail[0] else [bad,self.knowledge.model_dump_json()])
                async def run(*args,**options):
                    return SimpleNamespace(text=next(replies),usage_details={'total_token_count':1})
                return await model_agents.run_json(SimpleNamespace(name='分析官',run=run),prompt,schema,**kwargs)
            return await self.model(agent,prompt,schema,**kwargs)
        with patch.object(pipeline,'run_json',side_effect=model):
            with self.assertRaises(model_agents.StructuredOutputError):
                await self.run_task(execution_mode='standard')
            self.assertEqual(self.checkpoint()['completed_stages'],['planning','collecting'])
            before=list(self.searches)
            fail[0]=False
            summary=await self.run_task(resume=True,execution_mode='standard')
            self.assertEqual(self.searches,before)
            self.assertEqual(self.checkpoint()['status'],'done')
            self.assertEqual(len(summary['reports']),1)

    async def test_quick_four_model_runs_and_independent_final_check(self):
        summary=await self.run_task()
        self.assertEqual(self.calls,Counter({'ResearchPlan':1,'QuickSynthesis':1,'report':1,'VerificationReport':1}))
        self.assertEqual(self.checkpoint()['status'],'done')
        self.assertEqual(len(summary['reports']),1)

    async def test_plan_revision_can_be_confirmed_even_if_model_returns_same_plan(self):
        decisions=iter(['补充时间范围',''])
        async def confirm(_):return next(decisions)
        summary=await self.run_task(confirm=confirm)
        self.assertEqual(len(summary['reports']),1)
        self.assertEqual(self.calls['ResearchPlan'],2)

    async def test_no_search_results_fails_without_paid_fallback(self):
        async def empty(*args,**kwargs):return []
        with patch.object(search_sources.MCPWebSearchClient,'search',new=empty),patch.object(pipeline,'websearch_mcp_session',side_effect=AssertionError('unexpected paid fallback')):
            with self.assertRaisesRegex(RuntimeError,'未取得资料'):await self.run_task()
        self.assertEqual(self.checkpoint()['completed_stages'],['planning'])

    async def test_redo_analysis_reuses_search_and_rechecks(self):
        first=[True]
        actual=self.model
        async def model(*args,**kwargs):
            value=await actual(*args,**kwargs)
            if args[2] is VerificationReport and first[0]:
                first[0]=False
                return VerificationReport(verdict='fail',summary='需补充',issues=[VerificationIssue(claim='缺少说明',verdict='存疑',explanation='补充',severity='中')]),'',{}
            return value
        async def gate(_):return {'decision':'redo','feedback':'补充依据'}
        with patch.object(pipeline,'run_json',side_effect=model):await self.run_task(execution_mode='deep',gate=gate)
        self.assertEqual(self.calls['Knowledge'],2);self.assertEqual(len(self.searches),1)

    async def test_resume_failed_reasoning_keeps_prior_work(self):
        self.fail_schema=Analysis
        with self.assertRaises(ConnectionError):
            await self.run_task(execution_mode='standard')
        self.assertEqual(self.checkpoint()['completed_stages'],['planning','collecting','analyzing'])
        self.fail_schema=None
        await self.run_task(resume=True)
        self.assertEqual(self.calls['ResearchPlan'],1)
        self.assertEqual(self.calls['Knowledge'],1)
        self.assertEqual(self.calls['Analysis'],2)
        self.assertEqual(len(self.searches),1)

    async def test_http_timeout_disk_reload_and_resume(self):
        import httpx
        from dataclasses import replace
        from execution_policy import POLICIES
        original=POLICIES['standard']
        fail=[True]
        async def model(*args,**kwargs):
            if args[2] is Analysis and fail[0]:await asyncio.sleep(2)
            return await self.model(*args,**kwargs)
        async def wait_status(state,status):
            async def wait():
                while state.status!=status:
                    if state.task_obj and state.task_obj.done():raise AssertionError(f'Unexpected terminal status: {state.status}')
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait(),3)
        with patch.object(server,'runs',{}),patch.object(pipeline.config,'EVENTS_DIR',self.root),patch.object(pipeline,'run_json',side_effect=model),patch.dict(POLICIES,{'standard':replace(original,reasoning_seconds=.03)}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                result=await client.post('/api/runs',json={'task':'测试任务','execution_mode':'standard','auto_confirm':True})
                rid=result.json()['run_id'];state=server.runs[rid]
                await wait_status(state,'awaiting_sources')
                await client.post(f'/api/runs/{rid}/sources',json={'request_id':state.pending_sources['request_id'],'sources':['parallel']})
                await asyncio.wait_for(state.task_obj,3)
                self.assertEqual(state.status,'error')
                before=pipeline.load_checkpoint(rid)
                self.assertEqual(before['completed_stages'],['planning','collecting','analyzing'])
                server.runs.clear();server._restore_history_from_disk()
                restored=server.runs[rid];self.assertIsNot(restored,state)
                detail=(await client.get(f'/api/runs/{rid}')).json()
                self.assertEqual(detail['resume_info']['next_stage'],'reasoning')
                fail[0]=False;POLICIES['standard']=original
                response=await client.post(f'/api/runs/{rid}/resume',json={})
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.post(f'/api/runs/{rid}/resume',json={})).status_code,409)
                await asyncio.wait_for(restored.task_obj,3)
                self.assertEqual(restored.status,'done')
                self.assertEqual(self.calls['ResearchPlan'],1);self.assertEqual(self.calls['Knowledge'],1)
                self.assertEqual(len(self.searches),1);self.assertEqual(self.calls['report'],1)
                after=pipeline.load_checkpoint(rid)
                self.assertEqual(before['operations']['analyzing:produce:0'],after['operations']['analyzing:produce:0'])
                download=await client.get(f'/api/runs/{rid}/report.md')
                self.assertEqual(download.status_code,200);self.assertIn('测试报告',download.text)

    async def test_check_failure_does_not_regenerate_analysis(self):
        self.fail_check_number=1
        with self.assertRaises(ConnectionError):
            await self.run_task(execution_mode='deep')
        self.assertIn('analyzing:produce:0',self.checkpoint()['operations'])
        self.fail_check_number=None
        await self.run_task(resume=True)
        self.assertEqual(self.calls['Knowledge'],1)
        self.assertEqual(self.calls['ResearchPlan'],1)

    async def test_cancel_during_confirmation_restores_plan(self):
        async def cancel(_):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_task(confirm=cancel)
        self.assertEqual(self.checkpoint()['status'],'cancelled')
        await self.run_task(resume=True)
        self.assertEqual(self.calls['ResearchPlan'],1)

    async def test_resume_second_report_check_no_duplicate_appendix_or_calls(self):
        self.report_types=['第一份','第二份']
        self.fail_check_number=2
        with self.assertRaises(ConnectionError):
            await self.run_task(execution_mode='standard')
        self.fail_check_number=None
        summary=await self.run_task(resume=True)
        self.assertEqual(self.calls['report'],2)
        self.assertEqual(self.calls['VerificationReport'],3)
        for file in summary['reports']:
            self.assertEqual(Path(file).read_text(encoding='utf-8').count('## 附：事实核查'),1)

    async def test_rewrite_failure_restores_rewrite_intent(self):
        self.report_fail_once=True
        async def gate(check):
            self.fail_report=True
            return {'decision':'redo','feedback':'修正事实'}
        with self.assertRaises(ConnectionError):
            await self.run_task(gate=gate)
        row=self.checkpoint()['report_state']['简明解答']
        self.assertEqual(row['round'],1)
        self.assertNotIn('body',row)
        self.assertIn('修正事实',row['feedback'])
        self.fail_report=False
        await self.run_task(resume=True)
        self.assertEqual(self.calls['report'],3)
        self.assertEqual(self.calls['QuickSynthesis'],1)

    async def test_failed_query_retries_only_failure_after_restart(self):
        self.fail_schema=Knowledge
        async def sources(_):return ['parallel','you']
        fail=True
        async def search(_client,provider,query):
            self.searches.append(provider)
            if provider=='you' and fail:raise ConnectionError('no source')
            return [{'title':'source','source':'https://example.com/source','content':'真实片段','provider':provider}]
        # 取消收集关卡后恢复：成功查询已存盘，未完成收集阶段仍重新选渠道。
        async def emit(event):
            await self.emit(event)
            if event['type']=='gate_check' and event['stage']=='collecting' and fail:
                raise asyncio.CancelledError()
        with patch.object(search_sources.MCPWebSearchClient,'search',new=search):
            with self.assertRaises(asyncio.CancelledError):
                await pipeline.run_pipeline('研究','test',emit,self.confirm,run_id='test',select_sources=sources)
            fail=False;self.fail_schema=None
            await pipeline.run_pipeline('研究','test',self.emit,self.confirm,run_id='test',resume=True,select_sources=sources)
        self.assertEqual(self.searches.count('parallel'),1)
        self.assertEqual(self.searches.count('you'),2)


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        credential = patch.object(config, 'DASHSCOPE_API_KEY', 'test-budget-credential')
        credential.start()
        self.addCleanup(credential.stop)

    async def test_deep_reasoning_network_timeout_matches_operation_budget(self):
        from agents import build_agent
        self.assertEqual(POLICIES['deep'].timeout_for('reasoner'),360)
        self.assertEqual(POLICIES['standard'].timeout_for('reasoner'),120)
        agent=build_agent('deepseek-v4-pro-0813','test','推理官',role='reasoner',mode='deep')
        self.assertEqual(agent.client.client.timeout,370)
        await agent.client.client.close()
    async def test_deadline_cancels_underlying_work(self):
        cancelled=[]
        async def slow():
            try:await asyncio.sleep(1)
            finally:cancelled.append(True)
        budget=WorkBudget(.03)
        with self.assertRaises(ExecutionTimeout):await budget.run(slow,2,'test')
        self.assertEqual(cancelled,[True])

    async def test_human_wait_excluded(self):
        now=[0.0];budget=WorkBudget(10,lambda:now[0])
        async with budget.human_wait():now[0]=50
        self.assertEqual(budget.remaining,10)
        now[0]+=2
        self.assertEqual(budget.remaining,8)

    async def test_usage_includes_json_repair_and_sdk_retries_disabled(self):
        class Agent:
            name='test'
            def __init__(self):self.count=0
            async def run(self,*_,**kw):
                self.count+=1
                return SimpleNamespace(text='bad' if self.count==1 else '{"viewpoints":[]}',usage_details={'total_token_count':4})
        _,_,usage=await run_json(Agent(),'prompt',Knowledge)
        self.assertEqual(usage['total'],8)
        client=make_chat_client('qwen3.8-flash')
        self.assertEqual(client.client.max_retries,0)
        await client.client.close()

    async def test_single_report_shared_budget(self):
        from report_generation import generate_report
        class Reporter:
            def run(self,*_,**kw):
                async def stream():
                    await asyncio.sleep(1)
                    yield SimpleNamespace(text='x')
                return stream()
        async def noop(_):pass
        started=time.monotonic()
        with self.assertRaises((TimeoutError,asyncio.TimeoutError)):
            await generate_report(Reporter(),'x','x',noop,noop,lambda _:{},total_timeout=.03,backoff=0)
        self.assertLess(time.monotonic()-started,.4)


class ContextTests(unittest.TestCase):
    def test_report_citation_number_and_url_must_match(self):
        from pipeline_execution import report_reference_check
        context=json.dumps([{'id':'E1','source':'https://example.com/a'}])
        self.assertEqual(report_reference_check('[E1](https://example.com/a)',context),[])
        self.assertTrue(report_reference_check('[E2](https://example.com/a)',context))
        self.assertTrue(report_reference_check('[E1](https://other.test/a)',context))
    def test_bailian_page_count_unknown_is_not_zero(self):
        from agents import search_result_count
        self.assertEqual(search_result_count('{"pages":[{"url":"https://example.com"}],"status":0}'),1)
        self.assertEqual(search_result_count('{"pages":[],"status":0}'),0)
        self.assertEqual(search_result_count(SimpleNamespace(content=[SimpleNamespace(text='{"pages":[{},{}]}')])),2)
        self.assertIsNone(search_result_count('unrecognized text'))
        self.assertIsNone(search_result_count('{"pages":['))

    def test_url_dedup_query_coverage_and_input_bound(self):
        plan=ResearchPlan(goal='中文测试',subquestions=[SubQuestion(question='a'),SubQuestion(question='b')],report_types=['report'])
        rows=[EvidenceItem(subquestion='a',title='中文测试',source=f'https://example.com/{i}?utm_source=x',content='中文测试'*300) for i in range(20)]
        rows += [EvidenceItem(subquestion='b',title='中文测试',source='https://example.com/0',content='另一观点'),EvidenceItem(subquestion='b',title='中文测试',source='https://other.test/',content='补充观点')]
        context,stats=evidence_context(rows,plan,POLICIES['quick'])
        data=json.loads(context)
        self.assertEqual(stats['unique_count'],21)
        self.assertLessEqual(len(data),8)
        self.assertLessEqual(len(context),10000)
        self.assertTrue(any('b' in row['subquestions'] for row in data))
        self.assertTrue(any('另一观点' in row['content'] for row in data))

    def test_auto_depth_is_conservative(self):
        self.assertEqual(choose_policy('大肥鱼是什么意思').mode,'quick')
        self.assertEqual(choose_policy('大肥鱼是什么，形成对比报告').mode,'standard')
        self.assertEqual(choose_policy('深度调研当前产业政策').mode,'deep')


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_run_uses_selected_depth_profile(self):
        async def noop(*args,**kwargs):pass
        with patch.object(server,'_run_task',new=noop):
            for mode in ['quick','standard','deep']:
                result=await server.create_run(server.RunRequest(task='测试任务',execution_mode=mode))
                state=server.runs[result['run_id']]
                try:
                    await state.task_obj
                    self.assertEqual(state.role_models,models.role_defaults(mode))
                finally:server.runs.pop(state.id)

    async def test_profiles_match_recommendation_and_task_creation(self):
        for mode,task in [('quick','大肥鱼是什么'),('standard','调研并比较'),('deep','深度研究')]:
            recommendation=await server.model_recommendation(task,'auto')
            self.assertEqual(recommendation['mode'],mode)
            self.assertEqual(server.RunState(task,'test').role_models,recommendation['role_models'])
            explicit=server.validated_roles(None,mode)
            self.assertEqual(explicit,recommendation['role_models'])
            self.assertEqual(server.validated_roles({'reporter':'glm-5.3'},mode)['reporter'],'glm-5.3')
            for model in explicit.values():self.assertIsNotNone(models.get_model(model))
        self.assertNotEqual(models.role_defaults('quick'),models.role_defaults('standard'))
        self.assertNotEqual(models.role_defaults('standard'),models.role_defaults('deep'))
        self.assertIsNotNone(models.get_model('deepseek-v4.1-flash-1017'))

    async def test_fast_confirmation_and_gate_decision_not_lost(self):
        state=server.RunState('test','test')
        with patch.object(server,'_persist_events',new=AsyncMock()):
            emit=await server._emit_for(state)
            await emit({'type':'confirm_required','plan':{}})
            state.confirm_future.set_result('')
            confirm=await server._confirm_for(state)
            self.assertEqual(await asyncio.wait_for(confirm({}),.2),'')
            await emit({'type':'gate_check','stage':'planning','verdict':'fail','interactive':True})
            state.gate_future.set_result({'decision':'continue'})
            gate=await server._gate_for(state)
            self.assertEqual((await asyncio.wait_for(gate({}),.2))['decision'],'continue')

    async def test_invalid_roles_and_missing_checkpoint_guard(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):server.validated_roles({'planner':'missing'})
        with self.assertRaises(HTTPException):server.validated_roles({'bogus':'qwen3.8-flash'})
        state=server.RunState('test','test');state.status='error';server.runs[state.id]=state
        try:
            with patch.object(pipeline,'load_checkpoint',return_value=None):
                with self.assertRaises(HTTPException):await server.resume_run(state.id)
            self.assertEqual(state.status,'error')
        finally:server.runs.pop(state.id)

    async def test_incomplete_checkpoint_rejected_without_starting(self):
        from fastapi import HTTPException
        state=server.RunState('test','test');state.status='error';server.runs[state.id]=state
        try:
            checkpoint={'run_id':state.id,'task':'test','completed_stages':['planning']}
            with patch.object(pipeline,'load_checkpoint',return_value=checkpoint):
                self.assertTrue(state.to_detail()['resume_info']['unavailable'])
                with self.assertRaises(HTTPException) as caught:await server.resume_run(state.id)
                self.assertEqual(caught.exception.status_code,409)
                self.assertIsNone(state.task_obj)
                self.assertEqual(state.status,'error')
        finally:server.runs.pop(state.id)

    async def test_pipeline_preflight_error_is_visible_in_events(self):
        state=server.RunState('test','test')
        with patch.object(server,'_persist_events',new=AsyncMock()),patch.object(pipeline,'run_pipeline',side_effect=ValueError('injected invalid checkpoint')):
            await server._run_task(state,resume=True)
        self.assertEqual(state.status,'error')
        self.assertEqual(state.events[-1]['type'],'error')
        self.assertIn('invalid checkpoint',state.events[-1]['message'])

    async def test_resume_resets_pending_and_prevents_double_start(self):
        from fastapi import HTTPException
        state=server.RunState('test','test');state.status='error';state.pending_gate={'old':True}
        server.runs[state.id]=state
        async def waiting(*_,**kw):await asyncio.sleep(30)
        try:
            with patch.object(pipeline,'load_checkpoint',return_value={'run_id':state.id,'task':'test','completed_stages':[]}),patch.object(server,'_run_task',new=waiting):
                await server.resume_run(state.id,server.ResumeRequest(use_recommended=True))
                self.assertIsNone(state.pending_gate)
                self.assertEqual(state.role_models,models.role_defaults('standard'))
                with self.assertRaises(HTTPException):await server.resume_run(state.id)
        finally:
            if state.task_obj:
                state.task_obj.cancel();await asyncio.gather(state.task_obj,return_exceptions=True)
            server.runs.pop(state.id)


if __name__=='__main__':unittest.main()
