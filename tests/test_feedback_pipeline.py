"""有限补搜闭环的真实编排测试：模型和外部搜索全部替换为固定数据。"""
import unittest
from unittest.mock import patch

import pipeline
import search_sources
from execution_policy import POLICIES
from execution_runtime import WorkBudget
from research_feedback import supplement
from schemas import EvidenceGap, Knowledge, VerificationReport
import test_execution as fixtures


class FeedbackPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.Fixture()
        await self.f.asyncSetUp()
        self.prompts = []
        self.f.knowledge.evidence_gaps = [EvidenceGap(subquestion='测试问题', reason='需要原始记录核对')]
        self.f.analysis.evidence_gaps = [EvidenceGap(subquestion='测试问题', reason='需要原始记录核对')]
        original = self.f.model
        async def model(agent, prompt, schema, **kwargs):
            self.prompts.append((schema.__name__, prompt))
            return await original(agent, prompt, schema, **kwargs)
        self.f.patches.enter_context(patch.object(pipeline, 'run_json', side_effect=model))
        owner = self
        async def search(client, provider, text):
            owner.f.searches.append((provider, text))
            if '独立来源' in text:
                return [{'title':'测试问题原始记录', 'source':'https://original.example/record',
                         'content':'补搜取得的原始试验记录，详细提供测试问题的观测过程与边界条件。', 'provider':provider}]
            return [{'title':'测试问题公开说明', 'source':'https://example.com/source',
                     'content':'这份公开说明记录测试问题的方案背景和实施步骤，内容可供进一步核对。', 'provider':provider},
                    {'title':'测试问题实验结果', 'source':'https://second.example/result',
                     'content':'针对测试问题另一团队进行了实验，通过具体观测数据讨论适用条件与局限。', 'provider':provider}]
        self.f.patches.enter_context(patch.object(search_sources.MCPWebSearchClient,'search',new=search))

    async def asyncTearDown(self):
        await self.f.asyncTearDown()

    async def test_adaptive_feedback_searches_once_and_adds_no_model_calls(self):
        result = await self.f.run_task(execution_mode='standard',search_strategy='adaptive')
        extra = [(p,q) for p,q in self.f.searches if '独立来源' in q]
        self.assertEqual(len(extra),1)
        self.assertEqual(extra[0][0],'parallel')
        self.assertEqual(len(self.f.searches),2)
        self.assertEqual(self.f.calls['Knowledge'],1)
        self.assertEqual(self.f.calls['Analysis'],1)
        self.assertEqual(self.f.calls['report'],1)
        self.assertEqual(self.f.calls['VerificationReport'],1)
        cp = self.f.checkpoint()
        self.assertTrue(cp['feedback_state']['测试问题']['applied'])
        self.assertEqual(len(cp['evidence']),3)
        self.assertTrue(any('original.example/record' in prompt for name,prompt in self.prompts if name=='Analysis'))
        self.assertEqual(result['resource_usage']['supplemental_searches'],1)
        self.assertEqual(result['resource_usage']['search_calls'],2)
        self.assertTrue(result['unresolved_gaps'],'补搜返回不等于缺口已得到语义验证')

    async def test_legacy_preserves_search_semantics_and_keeps_gap_in_final_report(self):
        result = await self.f.run_task(execution_mode='standard',search_strategy='legacy')
        self.assertEqual(len(self.f.searches),1)
        self.assertFalse(any(e.get('action')=='COLLECT_FEEDBACK' for e in self.f.events))
        self.assertTrue(result['unresolved_gaps'])
        from pathlib import Path
        self.assertIn('需要原始记录核对',Path(result['reports'][0]).read_text(encoding='utf-8'))

    async def test_quick_path_keeps_combined_model_and_supports_bounded_feedback(self):
        await self.f.run_task(execution_mode='quick',search_strategy='adaptive')
        self.assertEqual(self.f.calls['QuickSynthesis'],1)
        self.assertEqual(self.f.calls['Knowledge'],0)
        self.assertEqual(self.f.calls['Analysis'],0)
        self.assertLessEqual(len([q for _,q in self.f.searches if '独立来源' in q]),1)

    async def test_no_free_provider_or_exhausted_allowance_never_searches(self):
        from schemas import ResearchPlan,SubQuestion
        plan=ResearchPlan(goal='测试',subquestions=[SubQuestion(question='测试问题')],report_types=['报告'])
        check=VerificationReport(verdict='pass',summary='结构可用')
        events=[]
        async def emit(e):events.append(e)
        async def save():pass
        async def forbidden(*args):raise AssertionError('不得自动增加付费/超额搜索')
        for selected, cp, seconds in [(['bailian'],{},100),(['parallel'],{'adaptive_state':{'测试问题':{'extra':2}}},100),(['parallel'],{},7)]:
            with self.subTest(selected=selected,cp=cp,seconds=seconds):
                rows=await supplement(self.f.knowledge,check,'analyzing',plan,selected,forbidden,cp,save,emit,WorkBudget(seconds),POLICIES['standard'])
                self.assertEqual(rows,[])
                self.assertTrue(cp['unresolved_gaps'])
        self.assertTrue(all(e['action']=='STOP_SEARCH' for e in events))

    async def test_failed_supplement_has_only_one_manual_resume_retry(self):
        from schemas import ResearchPlan,SubQuestion
        plan=ResearchPlan(goal='测试',subquestions=[SubQuestion(question='测试问题')],report_types=['报告'])
        check=VerificationReport(verdict='pass',summary='结构可用')
        calls=[];cp={};events=[]
        async def search(*args):calls.append(args);raise TimeoutError('模拟MCP超时')
        async def save():pass
        async def emit(e):events.append(e)
        for _ in range(4):
            self.assertEqual(await supplement(self.f.knowledge,check,'reasoning',plan,['parallel'],search,cp,save,emit,WorkBudget(100),POLICIES['standard']),[])
        self.assertEqual(len(calls),2)
        self.assertEqual(cp['feedback_state']['测试问题']['status'],'failed')
        self.assertTrue(any(e['action']=='FEEDBACK_FAILED' for e in events))

    async def test_reopening_mcp_session_does_not_reset_task_provider_budget(self):
        original = search_sources.MCPWebSearchClient.__init__
        def one_call(client,*args,**kwargs):original(client,max_calls=1)
        with patch.object(search_sources.MCPWebSearchClient,'__init__',new=one_call):
            result=await self.f.run_task(execution_mode='standard',search_strategy='adaptive')
        self.assertEqual(len(self.f.searches),1,'补搜新建会话也不得绕过任务渠道额度')
        self.assertEqual(result['resource_usage']['search_calls'],1)
        self.assertEqual(result['resource_usage']['supplemental_searches'],0)
        self.assertTrue(any('搜索请求上限' in r for r in result['resource_usage']['stop_reasons']))
        self.assertEqual(self.f.checkpoint()['feedback_state']['测试问题']['status'],'failed')


if __name__=='__main__':unittest.main()
