"""跨阶段依赖与补搜的中断窗口回归；固定 fixture，无外部请求。"""
import asyncio
import copy
import json
import unittest
from collections import Counter
from unittest.mock import patch

import pipeline
from execution_policy import POLICIES
from execution_runtime import WorkBudget, signature
from pipeline_cache import bind_inputs
from research_feedback import requests_for, supplement
from schemas import EvidenceGap, EvidenceItem, Knowledge, ResearchPlan, SubQuestion, VerificationReport
import test_execution as fixtures


class FeedbackCommitTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_gap_does_not_displace_other_subquestion(self):
        plan = ResearchPlan(goal='测试', subquestions=[SubQuestion(question='q1'), SubQuestion(question='q2')], report_types=['报告'])
        value = Knowledge(evidence_gaps=[EvidenceGap(subquestion='q1', reason='缺少材料'),
                                        EvidenceGap(subquestion='q1', reason='缺少另一材料'),
                                        EvidenceGap(subquestion='q2', reason='来源不足')])
        check = VerificationReport(verdict='pass', summary='可用')
        self.assertEqual([r['subquestion'] for r in requests_for(value, check, plan)], ['q1', 'q2'])

    async def test_success_status_survives_progress_emit_failure(self):
        plan = ResearchPlan(goal='测试', subquestions=[SubQuestion(question='q')], report_types=['报告'])
        value = Knowledge(evidence_gaps=[EvidenceGap(subquestion='q', reason='缺少原始资料')])
        row = EvidenceItem(subquestion='q', title='原始资料', source='https://source.example/a', content='补充资料').model_dump()
        checkpoint = {'evidence': []}
        async def save():
            pass
        async def search(*args):
            return [row]
        async def emit(event):
            if event.get('action') == 'FEEDBACK_RESULT':
                raise ConnectionError('fixture progress delivery failed')
        with self.assertRaises(ConnectionError):
            await supplement(value, VerificationReport(verdict='pass', summary='可用'), 'analyzing', plan,
                             ['parallel'], search, checkpoint, save, emit, WorkBudget(100), POLICIES['standard'])
        self.assertEqual(checkpoint['feedback_state']['q']['status'], 'success')
        self.assertEqual(checkpoint['feedback_state']['q']['rows'], [row])

    async def test_successful_supplement_replays_unarchived_rows_after_interruption(self):
        plan = ResearchPlan(goal='测试', subquestions=[SubQuestion(question='测试问题', search_queries=['测试'])], report_types=['报告'])
        value = Knowledge(evidence_gaps=[EvidenceGap(subquestion='测试问题', reason='缺少原始资料')])
        check = VerificationReport(verdict='pass', summary='结构可用')
        row = EvidenceItem(subquestion='测试问题', title='原始资料', source='https://source.example/a',
                           content='这是补充搜索返回的独立资料，应该在恢复后仍被归档。').model_dump()
        checkpoint = {'evidence': [], 'adaptive_state': {}}
        persisted = []
        calls = []
        interrupt = True
        async def search(*args):
            calls.append(args)
            return [row]
        async def save():
            persisted.append(copy.deepcopy(checkpoint))
        async def emit(event):
            if event.get('action') == 'FEEDBACK_RESULT' and interrupt:
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await supplement(value, check, 'analyzing', plan, ['parallel'], search, checkpoint,
                             save, emit, WorkBudget(100), POLICIES['standard'])
        # 模拟进程从最新已保存检查点恢复，而不是沿用协程局部变量。
        checkpoint = json.loads(json.dumps(persisted[-1]))
        self.assertEqual(checkpoint['feedback_state']['测试问题']['status'], 'success')
        self.assertEqual(checkpoint['evidence'], [])
        interrupt = False
        recovered = await supplement(value, check, 'analyzing', plan, ['parallel'], search, checkpoint,
                                     save, emit, WorkBudget(100), POLICIES['standard'])
        self.assertEqual(recovered, [row], '已保存但未归档的补搜结果必须重放给流水线')
        self.assertEqual(len(calls), 1, '恢复补搜结果不能重新访问工具')
        checkpoint['evidence'].extend(recovered)
        self.assertEqual(await supplement(value, check, 'analyzing', plan, ['parallel'], search, checkpoint,
                                          save, emit, WorkBudget(100), POLICIES['standard']), [])
        self.assertEqual(len(calls), 1)


class DependencyHashTests(unittest.TestCase):
    def test_unchanged_inputs_keep_downstream_and_changed_inputs_invalidate_once(self):
        cp = {'completed_stages': ['planning', 'collecting', 'analyzing', 'reasoning', 'reporting', 'verifying'],
              'knowledge': {'old': 1}, 'analysis': {'old': 2}, 'reports': ['old.md'],
              'report_state': {'r': {'body': 'old'}}, 'operations': {'saved': {'value': 1}}}
        self.assertEqual(bind_inputs(cp, 'analyzing', ['task', 'old evidence']), [])
        self.assertEqual(bind_inputs(cp, 'analyzing', ['task', 'old evidence']), [])
        self.assertIn('reasoning', cp['completed_stages'])
        self.assertEqual(bind_inputs(cp, 'analyzing', ['task', 'new evidence']),
                         ['analyzing', 'reasoning', 'reporting', 'verifying'])
        self.assertEqual(cp['completed_stages'], ['planning', 'collecting'])
        self.assertNotIn('knowledge', cp)
        self.assertNotIn('analysis', cp)
        self.assertNotIn('report_state', cp)
        self.assertEqual(cp['operations'], {'saved': {'value': 1}})
        self.assertEqual(bind_inputs(cp, 'analyzing', ['task', 'new evidence']), [])

    def test_legacy_checkpoint_registers_dependency_without_deleting_successes(self):
        cp = {'completed_stages': ['planning', 'collecting', 'analyzing'], 'knowledge': {'saved': 1}}
        self.assertEqual(bind_inputs(cp, 'analyzing', ['old task', 'current evidence']), [])
        self.assertEqual(cp['knowledge'], {'saved': 1})
        self.assertIn('analyzing', cp['completed_stages'])


class PipelineDependencies(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.Fixture()
        await self.fixture.asyncSetUp()
        self.prompts = {}
        original = self.fixture.model
        async def model(agent, prompt, schema, **kwargs):
            self.prompts[schema.__name__] = prompt
            return await original(agent, prompt, schema, **kwargs)
        self.fixture.patches.enter_context(patch.object(pipeline, 'run_json', side_effect=model))

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def interrupted_after_saved_operation(self, target):
        original = self.fixture.emit
        async def emit(event):
            await original(event)
            if event.get('type') == 'operation_done' and event.get('operation') == target:
                raise asyncio.CancelledError()
        with patch.object(self.fixture, 'emit', new=emit):
            with self.assertRaises(asyncio.CancelledError):
                await self.fixture.run_task(execution_mode='standard')

    async def test_same_model_reuses_uncommitted_valid_produce_result(self):
        await self.interrupted_after_saved_operation('analyzing:produce:0')
        self.assertNotIn('analyzing', self.fixture.checkpoint()['completed_stages'])
        self.assertIn('analyzing:produce:0', self.fixture.checkpoint()['operations'])
        await self.fixture.run_task(resume=True)
        self.assertEqual(self.fixture.calls['Knowledge'], 1)
        self.assertEqual(len(self.fixture.searches), 1)

    async def test_changed_analyst_model_invalidates_uncommitted_produce_only(self):
        await self.interrupted_after_saved_operation('analyzing:produce:0')
        old = self.fixture.checkpoint()['role_models']['analyst']
        replacement = 'glm-5.3' if old != 'glm-5.3' else 'qwen3.8-flash'
        await self.fixture.run_task(resume=True, role_models_override={'analyst': replacement})
        self.assertEqual(self.fixture.calls['Knowledge'], 2)
        self.assertEqual(self.fixture.calls['ResearchPlan'], 1)
        self.assertEqual(len(self.fixture.searches), 1)

    async def test_changed_reporter_model_invalidates_uncommitted_report_cache(self):
        await self.interrupted_after_saved_operation('report:简明解答:produce:0')
        old = self.fixture.checkpoint()['role_models']['reporter']
        replacement = 'glm-5.3' if old != 'glm-5.3' else 'qwen3.8-flash'
        await self.fixture.run_task(resume=True, role_models_override={'reporter': replacement})
        self.assertEqual(self.fixture.calls['report'], 2)
        self.assertEqual(self.fixture.calls['Knowledge'], 1)
        self.assertEqual(len(self.fixture.searches), 1)

    async def test_old_unversioned_operation_signature_reuses_valid_same_model_output(self):
        await self.interrupted_after_saved_operation('analyzing:produce:0')
        cp = self.fixture.checkpoint()
        cp.pop('stage_inputs', None)
        cp['operations']['analyzing:produce:0']['signature'] = signature([self.prompts['Knowledge'], 'Knowledge'])
        cp.pop('operation_attempts', None)
        await pipeline.save_checkpoint('test', cp)
        await self.fixture.run_task(resume=True)
        self.assertEqual(self.fixture.calls['Knowledge'], 1,
                         '已知同模型且当前提示匹配旧格式签名时，不应重复付费生成')
        self.assertEqual(len(self.fixture.searches), 1)

    async def test_evidence_change_invalidates_downstream_once_and_reuses_search(self):
        self.fixture.fail_schema = fixtures.Analysis
        with self.assertRaises(ConnectionError):
            await self.fixture.run_task(execution_mode='standard')
        cp = self.fixture.checkpoint()
        cp['evidence'].append(EvidenceItem(subquestion='测试问题', title='补充材料',
                           source='https://new.example/context', content='新增资料可以改变研究上下文，请根据补充内容重新整理观点。').model_dump())
        await pipeline.save_checkpoint('test', cp)
        # 第二次在推理处继续失败；第三次应该复用第二次已重算的分析，而非反复重算。
        with self.assertRaises(ConnectionError):
            await self.fixture.run_task(resume=True)
        self.assertEqual(self.fixture.calls['Knowledge'], 2)
        self.fixture.fail_schema = None
        await self.fixture.run_task(resume=True)
        self.assertEqual(self.fixture.calls['Knowledge'], 2)
        self.assertEqual(self.fixture.calls['ResearchPlan'], 1)
        self.assertEqual(len(self.fixture.searches), 1)


if __name__ == '__main__':
    unittest.main()
