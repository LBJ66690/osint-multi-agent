"""可比较的显式差异才产生线索；线索始终不等于已证伪。"""
import unittest
from evidence_quality import conflicts


def pair(a, b):
    return [dict(subquestion='项目', source='https://a.org/a', content=a),
            dict(subquestion='项目', source='https://b.net/b', content=b)]


class GeneralConflictTests(unittest.TestCase):
    def test_existing_investment_values_and_unknown_status_are_compatible(self):
        found = conflicts(pair('2025年该项目总投资2.8亿元，包含一期建筑和建设配套设施。',
                               '2025年该项目总投资3.2亿元，包含一期建筑和建设配套设施。'))
        self.assertEqual(len(found), 1)
        self.assertEqual(set(found[0]['values']), {'28000.0', '32000.0'})
        self.assertEqual(found[0]['status'], 'uncertain')

    def test_numeric_disagreement_for_same_subject_year_and_metric(self):
        found = conflicts(pair('2025年甲公司营业收入为100亿元，详细数据来自年度财务报告。',
                               '2025年甲公司营业收入为120亿元，详细数据来自年度财务报告。'))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'numeric')
        self.assertEqual(found[0]['subject'], '甲公司')
        self.assertEqual(found[0]['topic'], '营业收入')
        self.assertEqual(found[0]['status'], 'uncertain')

    def test_different_subjects_same_question_do_not_conflict(self):
        self.assertFalse(conflicts(pair('2025年甲公司营收100亿元，附有详细说明。',
                                        '2025年乙公司营收120亿元，附有详细说明。')))

    def test_different_years_do_not_conflict(self):
        self.assertFalse(conflicts(pair('2024年甲公司营收100亿元，附有详细说明。',
                                        '2025年甲公司营收120亿元，附有详细说明。')))

    def test_different_scope_actual_forecast_and_periods_do_not_conflict(self):
        cases = [('甲公司2025年计划营收100亿元。', '甲公司2025年实际营收80亿元。'),
                 ('2025年甲公司上半年营收100亿元。', '2025年甲公司全年营收220亿元。'),
                 ('2025年甲公司合并口径营收100亿元。', '2025年甲公司母公司口径营收80亿元。'),
                 ('2025年甲项目总投资2亿元（一期）。','2025年甲项目总投资3亿元（二期）。')]
        for a, b in cases:
            with self.subTest(a=a, b=b):
                self.assertFalse(conflicts(pair(a, b)))

    def test_equivalent_units_do_not_conflict(self):
        self.assertFalse(conflicts(pair('2025年甲项目总投资1亿元，包含设备采购。',
                                        '2025年甲项目总投资10000万元，包含设备采购。')))
        self.assertFalse(conflicts(pair('2025年甲项目装机容量为1GW，附有设备数据。',
                                        '2025年甲项目装机容量为1000兆瓦，附有设备数据。')))

    def test_event_time_disagreement_is_marked_uncertain(self):
        found = conflicts(pair('甲项目于2025年5月1日正式开工，公告附有现场记录。',
                               '甲项目于2025年6月1日正式开工，公告附有现场记录。'))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'event_time')
        self.assertEqual(found[0]['topic'], '开工时间')
        self.assertEqual(found[0]['status'], 'uncertain')

    def test_same_date_with_iso_or_chinese_format_does_not_conflict(self):
        self.assertFalse(conflicts(pair('甲公司成立时间为2025年5月1日，资料包含登记记录。',
                                        '甲公司成立时间为2025-05-01，资料包含登记记录。')))

    def test_different_event_or_entity_does_not_conflict(self):
        self.assertFalse(conflicts(pair('甲项目于2025年5月1日正式开工。', '甲项目于2025年6月1日正式竣工。')))
        self.assertFalse(conflicts(pair('甲项目于2025年5月1日正式开工。', '乙项目于2025年6月1日正式开工。')))

    def test_opposite_factual_statements_are_only_risks(self):
        found = conflicts(pair('2025年甲项目已经获批，公告详细介绍了许可范围。',
                               '2025年甲项目尚未获批，公告详细介绍了许可范围。'))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'factual')
        self.assertEqual(set(found[0]['values']), {'肯定', '否定'})
        self.assertEqual(found[0]['status'], 'uncertain')

    def test_factual_change_across_years_is_not_automatic_conflict(self):
        self.assertFalse(conflicts(pair('2024年甲项目尚未获批，公告详细介绍许可范围。',
                                        '2025年甲项目已经获批，公告详细介绍许可范围。')))

    def test_no_comparable_subject_or_multi_year_numbers_are_not_guessed(self):
        self.assertFalse(conflicts(pair('营业收入100亿元。', '营业收入120亿元。')))
        self.assertFalse(conflicts(pair('2025年比2024年甲公司营收100亿元。', '2025年比2024年甲公司营收120亿元。')))

    def test_different_month_or_observation_day_does_not_conflict(self):
        self.assertFalse(conflicts(pair('2025年1月甲公司营收100亿元。','2025年2月甲公司营收120亿元。')))
        self.assertFalse(conflicts(pair('2025年5月1日甲项目尚未获批。','2025年5月2日甲项目已经获批。')))

    def test_month_only_and_exact_day_are_not_conflicting_precision(self):
        self.assertFalse(conflicts(pair('甲公司成立时间为2025年5月。','甲公司成立时间为2025年5月1日。')))

    def test_different_currencies_are_not_comparable(self):
        self.assertFalse(conflicts(pair('2025年甲公司营收1亿元。','2025年甲公司营收1亿美元。')))

    def test_opposite_capability_claims_are_detected(self):
        found=conflicts(pair('甲软件支持离线部署，官方列出详细说明。','甲软件不支持离线部署，官方列出详细说明。'))
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['topic'],'支持离线部署')
        self.assertEqual(found[0]['status'],'uncertain')


if __name__ == '__main__':
    unittest.main()
