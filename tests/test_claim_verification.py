"""引用匹配、材料支持风险和请求统计测试；所有模型响应均为固定模拟数据。"""
import asyncio
import json
import unittest
from types import SimpleNamespace

import agents
from evidence_verification import (structural_check, report_reference_check, finding_details,
                                   verification_appendix)
from report_generation import generate_report
from schemas import Analysis, Finding, Knowledge, VerificationReport, VerificationIssue, EvidenceItem


CONTEXT = [dict(id='E1', source='https://a.example/report', title='甲公司成立公告',
                content='甲公司成立于2024年，公告说明公司的注册主体和经营范围，未提供有关融资的数据。'),
           dict(id='E2', source='https://b.example/review', title='甲公司调查',
                content='另一份调查说明注册和业务启动的时间不同，甲公司尚未公开确认业务启动时间。')]


class ClaimTests(unittest.TestCase):
    def test_old_schemas_remain_readable_without_claiming_certainty(self):
        old = Finding(claim='旧记录', evidence=['片段'], sources=['https://a.example/report'])
        self.assertEqual(old.status, 'unconfirmed')
        self.assertEqual(old.support_ids, [])
        self.assertEqual(Knowledge().evidence_gaps, [])
        self.assertEqual(Analysis().evidence_gaps, [])
        self.assertEqual(VerificationReport(verdict='pass', summary='ok').scope, 'provided_evidence')

    def test_unknown_url_and_wrong_number_are_detected(self):
        for body in ['事实 [E1](https://unknown.example/x)', '事实 [E2](https://a.example/report)']:
            issues = report_reference_check(body, CONTEXT)
            self.assertTrue(issues)
            self.assertEqual(issues[0].check_type, 'reference')
            self.assertEqual(issues[0].severity, '高')

    def test_known_url_with_unrelated_content_prompts_support_risk(self):
        context = [dict(id='E1', source='https://a.example/report', title='种植资料',
                        content='山地果园介绍苹果种植技术、灌溉方式和病虫害防治经验，采用适合当地气候条件的方法。')]
        body = '海底火山喷发导致跨洋通信线路中断并影响国际航运。 [E1](https://a.example/report)'
        issues = report_reference_check(body, context)
        self.assertTrue(any(i.check_type == 'semantic_support' for i in issues))
        self.assertIn('不能据此判定事实真伪', issues[0].explanation)

    def test_known_url_with_obvious_attribute_mismatch_warns(self):
        issues = report_reference_check('甲公司成立于2025年。[E1](https://a.example/report)', CONTEXT)
        self.assertTrue(any('不同数值' in i.explanation for i in issues))

    def test_matching_citation_is_not_a_fact_correctness_certificate(self):
        self.assertEqual(report_reference_check('甲公司成立于2024年。[E1](https://a.example/report)', CONTEXT), [])
        text = verification_appendix('报告', VerificationReport(verdict='pass', summary='本次片段支持'))
        self.assertIn('未进行额外的外部独立事实调查', text)
        self.assertIn('通过不代表事实正确率', text)

    def test_different_reporting_years_do_not_trigger_numeric_conflict(self):
        context = [dict(id='E1', source='https://a.example/report', title='甲公司营收',
                        content='甲公司2024年营业收入为10亿元，统计覆盖该年度全部主营业务收入与相关服务收入。')]
        issues = report_reference_check('甲公司2025年营业收入为20亿元。[E1](https://a.example/report)', context)
        self.assertFalse(any('不同数值' in i.explanation for i in issues))

    def test_equivalent_units_and_different_subjects_do_not_trigger_numeric_conflict(self):
        context = [dict(id='E1', source='https://a.example/report', title='甲公司营收',
                        content='甲公司2025年营业收入为1亿元，统计覆盖该年度全部主营业务收入与相关服务收入。')]
        for claim in ['甲公司2025年营业收入为10000万元。', '乙公司2025年营业收入为20亿元。']:
            issues = report_reference_check(claim + '[E1](https://a.example/report)', context)
            self.assertFalse(any('不同数值' in i.explanation for i in issues))

    def test_metadata_alone_does_not_make_unsupported_fact_acceptable(self):
        value = Analysis(findings=[Finding(claim='甲公司已完成十亿元融资', status='unconfirmed',
                                          uncertainty_reason='没有取得融资公告')])
        self.assertEqual(structural_check('reasoning', value).verdict, 'fail')

    def test_unconfirmed_output_is_allowed_without_fabricating_sources(self):
        value = Analysis(findings=[Finding(claim='现有资料不足，无法确认该公司的准确注册时间。',
                                          uncertainty_reason='没有取得注册文件')])
        self.assertEqual(structural_check('reasoning', value).verdict, 'pass')

    def test_uncited_confirmed_claim_and_lost_uncertainty_warn(self):
        claim = '甲公司的业务已经在全国范围正式上线运营'
        analysis = Analysis(findings=[Finding(claim=claim, evidence=['调查片段'], sources=['https://a.example/report'],
                                             status='uncertain', counter_ids=['E2'])])
        issues = report_reference_check(claim, CONTEXT, analysis)
        self.assertTrue(any(i.check_type == 'reference' for i in issues))
        self.assertTrue(any(i.check_type == 'uncertainty' for i in issues))
        disclosed = claim + '的说法尚未确认，存在不同口径。 [E1](https://a.example/report)'
        self.assertFalse(any(i.check_type == 'uncertainty' for i in report_reference_check(disclosed, CONTEXT, analysis)))

    def test_explicit_support_and_counter_ids_preserved_without_fake_confidence(self):
        analysis = Analysis(findings=[Finding(claim='注册与实际运营应区分', sources=['https://a.example/report'],
                                             support_ids=['E1'], counter_ids=['E2'], status='inference')])
        row = finding_details(analysis, CONTEXT, {})[0]
        self.assertEqual(row['support_ids'], ['E1'])
        self.assertEqual(row['counter_ids'], ['E2'])
        self.assertEqual(row['confidence'], 'unknown')
        self.assertEqual(row['link_status'], 'explicit_mapping')
        self.assertTrue(row['uncertain'])

    def test_conflict_overrides_deterministic_supported_status(self):
        analysis = Analysis(findings=[Finding(claim='已确定', sources=['https://a.example/report'], status='supported')])
        row = finding_details(analysis, CONTEXT, {'conflicts': [{'values': {'2024': ['https://a.example/report'], '2025': ['https://b.example/review']}}]})[0]
        self.assertTrue(row['uncertain'])
        self.assertEqual(row['status'], 'uncertain')
        self.assertEqual(row['confidence'], 'low')

    def test_invalid_structured_evidence_id_is_not_silently_dropped(self):
        analysis = Analysis(findings=[Finding(claim='结论', sources=['https://a.example/report'], support_ids=['E99'])])
        issues = report_reference_check('结论', CONTEXT, analysis)
        self.assertTrue(any('编号' in i.explanation for i in issues))

    def test_existing_support_id_must_match_declared_source_url(self):
        analysis = Analysis(findings=[Finding(claim='结论', sources=['https://b.example/review'], support_ids=['E1'])])
        issues = report_reference_check('结论', CONTEXT, analysis)
        mismatch = next(i for i in issues if '来源URL不匹配' in i.explanation)
        self.assertEqual(mismatch.check_type, 'reference')
        self.assertEqual(mismatch.evidence_ids, ['E1'])
        self.assertEqual(mismatch.severity, '高')

    def test_counter_id_can_point_to_a_separate_source(self):
        analysis = Analysis(findings=[Finding(claim='无法确认', sources=['https://a.example/report'],
                                             support_ids=['E1'], counter_ids=['E2'])])
        self.assertEqual(report_reference_check('无法确认', CONTEXT, analysis), [])
        row = finding_details(analysis, CONTEXT, {})[0]
        self.assertEqual(row['counter_ids'], ['E2'])
        self.assertEqual(row['link_status'], 'explicit_mapping')

    def test_invalid_support_id_survives_enrichment_and_final_validation(self):
        analysis = Analysis(findings=[Finding(claim='结论', sources=['https://a.example/report'], support_ids=['E99'])])
        findings = finding_details(analysis, CONTEXT, {})
        self.assertEqual(findings[0]['support_ids'], ['E99'])
        self.assertEqual(findings[0]['linked_ids'], ['E1'])
        self.assertEqual(findings[0]['link_status'], 'invalid_mapping')
        self.assertEqual(findings[0]['status'], 'uncertain')
        self.assertIn('E99', findings[0]['uncertainty_reason'])
        self.assertTrue(any('编号不在' in i.explanation for i in report_reference_check('结论', CONTEXT, {'findings': findings})))

    def test_mismatched_support_id_is_not_replaced_with_linked_source(self):
        analysis = Analysis(findings=[Finding(claim='结论', sources=['https://b.example/review'], support_ids=['E1'], status='supported')])
        findings = finding_details(analysis, CONTEXT, {})
        self.assertEqual(findings[0]['support_ids'], ['E1'])
        self.assertEqual(findings[0]['linked_ids'], ['E2'])
        self.assertEqual(findings[0]['link_status'], 'invalid_mapping')
        self.assertTrue(any('来源URL不匹配' in i.explanation for i in report_reference_check('结论', CONTEXT, {'findings': findings})))

    def test_invalid_counter_id_is_kept_for_review(self):
        analysis = Analysis(findings=[Finding(claim='无法确认', sources=['https://a.example/report'],
                                             support_ids=['E1'], counter_ids=['E99'])])
        findings = finding_details(analysis, CONTEXT, {})
        self.assertEqual(findings[0]['counter_ids'], ['E99'])
        self.assertTrue(any('编号不在' in i.explanation for i in report_reference_check('无法确认', CONTEXT, {'findings': findings})))

    def test_support_mapping_uses_canonical_url(self):
        analysis = Analysis(findings=[Finding(claim='结论', sources=['https://a.example/report/?utm_campaign=demo#section'], support_ids=['E1'])])
        self.assertEqual(report_reference_check('结论', CONTEXT, analysis), [])

    def test_unresolved_issues_survive_appendix(self):
        issues = report_reference_check('[E99](https://a.example/report)', CONTEXT)
        text = verification_appendix('报告', VerificationReport(verdict='fail', summary='引用错配', issues=issues))
        self.assertIn('尚未解决的问题', text)
        self.assertIn('引用编号或URL', text)

    def test_pass_with_issues_is_conservatively_normalized_without_dropping_issues(self):
        issue = VerificationIssue(claim='关键日期', verdict='存疑', explanation='两份片段日期不同', severity='高')
        check = VerificationReport(verdict='pass', summary='模型原始判定', issues=[issue])
        self.assertEqual(check.verdict, 'fail')
        self.assertEqual(check.issues, [issue])
        self.assertIn('判定与问题清单不一致', check.summary)
        self.assertIn('模型原始判定', check.summary)
        self.assertEqual(VerificationReport.model_validate(check.model_dump()).summary, check.summary)

    def test_legacy_empty_issues_and_failed_reports_keep_their_verdict(self):
        for verdict in ['pass', 'fail']:
            with self.subTest(verdict=verdict):
                check = VerificationReport.model_validate({'verdict': verdict, 'summary': '旧记录'})
                self.assertEqual(check.verdict, verdict)
                self.assertEqual(check.issues, [])


class UsageEventsTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_usage_is_unknown_and_real_zero_stays_zero(self):
        self.assertEqual(agents._extract_usage(SimpleNamespace()), {'prompt': None, 'completion': None, 'total': None})
        self.assertEqual(agents._extract_usage(SimpleNamespace(usage_details={'input_token_count': 0, 'output_token_count': 0})),
                         {'prompt': 0, 'completion': 0, 'total': 0})
        self.assertEqual(agents._extract_usage(SimpleNamespace(usage_details={'total_token_count': 7})),
                         {'prompt': None, 'completion': None, 'total': 7})

    async def test_invalid_json_responses_count_usage_before_validation(self):
        values = iter(['bad json', '{"viewpoints":[]}'])
        async def run(*args, **kwargs):
            return SimpleNamespace(text=next(values), usage_details={'total_token_count': 3})
        events = []
        async def emit(event): events.append(event)
        await agents.run_json(SimpleNamespace(name='分析官', run=run), 'test', Knowledge, emit=emit, stage='analyzing', operation='analyzing:produce:0')
        requests = [e for e in events if e['type'] == 'model_request']
        responses = [e for e in events if e['type'] == 'model_response']
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(responses), 2)
        self.assertEqual(sum(e['usage']['total'] for e in responses), 6)
        self.assertEqual(len({e['request_id'] for e in requests}), 2)
        self.assertEqual(len([e for e in events if e['type'] == 'model_parse_error']), 1)
        self.assertEqual(requests[0]['role'], 'analyst')

    async def test_failed_and_cancelled_requests_are_visible(self):
        async def emit(event): events.append(event)
        async def fail(*args, **kwargs): raise ValueError('permission')
        events = []
        with self.assertRaises(ValueError):
            await agents.run_json(SimpleNamespace(name='分析官', run=fail), 'test', Knowledge, emit=emit)
        self.assertEqual(len([e for e in events if e['type'] == 'model_error']), 1)
        self.assertFalse([e for e in events if e['type'] == 'model_error'][0]['cancelled'])
        async def cancel(*args, **kwargs): raise asyncio.CancelledError()
        events = []
        with self.assertRaises(asyncio.CancelledError):
            await agents.run_json(SimpleNamespace(name='分析官', run=cancel), 'test', Knowledge, emit=emit)
        self.assertTrue([e for e in events if e['type'] == 'model_error'][0]['cancelled'])

    async def test_report_requests_emit_actual_usage_and_no_extra_calls(self):
        calls = []
        async def run(*args, **kwargs):
            calls.append(1)
            return SimpleNamespace(text='report', usage_details={'input_token_count': 5, 'output_token_count': 2})
        events = []
        async def emit(event): events.append(event)
        async def save(text): pass
        result = await generate_report(SimpleNamespace(run=run), 'prompt', 'report', emit, save, agents._extract_usage, attempts=0)
        self.assertEqual(result, 'report')
        self.assertEqual(len(calls), 1)
        response = [e for e in events if e['type'] == 'model_response'][0]
        self.assertEqual(response['usage']['total'], 7)
        self.assertEqual(response['role'], 'reporter')

    async def test_exhausted_report_budget_does_not_count_an_unsent_request(self):
        async def run(*args, **kwargs): raise AssertionError('must not call model')
        events = []
        async def emit(event): events.append(event)
        async def save(text): pass
        with self.assertRaises(TimeoutError):
            await generate_report(SimpleNamespace(run=run), 'prompt', 'report', emit, save, agents._extract_usage,
                                  attempts=0, total_timeout=0)
        self.assertFalse(any(e['type'] in {'model_request', 'model_response', 'model_error'} for e in events))

    async def test_contradictory_verdict_needs_no_extra_json_request(self):
        calls = []
        async def run(*args, **kwargs):
            calls.append(1)
            return SimpleNamespace(text=json.dumps({'verdict': 'pass', 'summary': '原始结论',
                'issues': [{'claim': '日期', 'verdict': '存疑', 'explanation': '不一致', 'severity': '高'}]}, ensure_ascii=False))
        value, _, _ = await agents.run_json(SimpleNamespace(name='核查官', run=run), 'test', VerificationReport)
        self.assertEqual(value.verdict, 'fail')
        self.assertEqual(len(calls), 1)

    async def test_pipeline_gate_consumes_normalized_fail_and_preserves_accepted_risks(self):
        import pipeline
        from unittest.mock import patch
        from test_execution import Fixture
        fixture = Fixture()
        await fixture.asyncSetUp()
        gates = []
        original = fixture.model
        async def model(*args, **kwargs):
            value = await original(*args, **kwargs)
            if args[2] is VerificationReport:
                return VerificationReport(verdict='pass', summary='原始判定', issues=[VerificationIssue(
                    claim='注册日期存在不同口径', verdict='存疑', explanation='仍需注册文件核对', severity='高')]), '', {}
            return value
        async def gate(check):
            gates.append(check)
            return {'decision': 'continue', 'feedback': '用户接受保留未解决问题'}
        try:
            with patch.object(pipeline, 'run_json', side_effect=model):
                summary = await fixture.run_task(gate=gate)
            self.assertEqual(len(gates), 1)
            self.assertEqual(gates[0]['verdict'], 'fail')
            check = next(iter(summary['verifications'].values()))
            self.assertEqual(check['verdict'], 'fail')
            self.assertEqual(check['issues'][0]['claim'], '注册日期存在不同口径')
            self.assertEqual(fixture.calls['VerificationReport'], 1)
            text = fixture.root.joinpath(summary['reports'][0]).read_text(encoding='utf-8')
            self.assertIn('尚未解决的问题', text)
            self.assertIn('仍需注册文件核对', text)
        finally:
            await fixture.asyncTearDown()


if __name__ == '__main__':
    unittest.main()
