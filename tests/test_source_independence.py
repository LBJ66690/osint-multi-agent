"""用离线片段验证转载关系，不将域名数等同于独立事实核验。"""
import copy
import unittest

from evidence_quality import assess, curate, information_gain, provenance, similar


def row(source, content, **extra):
    return dict(subquestion='项目', title='项目进度', source=source, content=content, **extra)


class SourceIndependenceTests(unittest.TestCase):
    def test_reprints_of_same_wrong_origin_remain_one_source_chain(self):
        rows = [row('https://a.org/story', '来源：甲通讯社\n甲项目已经取得所有许可并开工，该报道未给出公告编号，相关文字转载自同一条消息。'),
                row('https://b.net/story', '来源：甲通讯社\n甲项目宣称具备施工手续，但该站仅转述消息，没有新增独立采访或原始记录可供核对。')]
        before = copy.deepcopy(rows)
        quality = assess(rows, ['项目'])
        self.assertEqual(quality['source_group_count'], 1)
        self.assertEqual(quality['coverage']['项目']['status'], 'weak')
        self.assertIsNone(quality['independent_source_count'])
        self.assertEqual({p['independence_status'] for p in quality['provenance']}, {'shared_origin'})
        self.assertEqual(len(curate(rows)), 2, '同一出处的不同表述仍有分析价值')
        self.assertEqual(rows, before, '归档内容不得被标注或去重操作修改')

    def test_near_identical_reprint_with_publisher_labels_is_duplicate(self):
        body = ('甲项目施工单位披露，项目已经完成基础结构设计和现场勘测，'
                '后续将分阶段开展设备安装和联合调试。公告说明了测试方法、'
                '项目范围以及实施条件，同时保留了未确认事项供公众核对。')
        first = row('https://a.org/story', '来源：甲通讯社\n' + body)
        second = row('https://b.net/reprint', '来源：甲通讯社\n' + body + '\n编辑：小李')
        self.assertTrue(similar(first, second))
        self.assertEqual(len(curate([first, second])), 1)
        self.assertEqual(information_gain([first], [second])['level'], 'low')

    def test_different_original_work_is_kept_but_independence_not_certified(self):
        rows = [row('https://a.org/method', '本研究通过现场独立实测，获得了项目设备的连续工作记录，并在方法部分说明了仪器校准和误差范围。'),
                row('https://b.net/interview', '我们独立采访了项目周边居民，形成了关于施工噪声和生活影响的访谈记录，观点与工程测试不同。')]
        quality = assess(rows, ['项目'])
        self.assertEqual(len(curate(rows)), 2)
        self.assertEqual(quality['coverage']['项目']['status'], 'sufficient')
        self.assertEqual(quality['source_group_count'], 2)
        self.assertIsNone(quality['independent_source_count'])
        self.assertTrue(all(p['independence_status'] == 'unknown' for p in quality['provenance']))
        self.assertEqual({p['origin_type'] for p in quality['provenance']}, {'original_claim'})

    def test_authority_suffix_does_not_certify_source_or_claim(self):
        quality = assess([row('https://notice.gov.cn/a', '该项目尚未公布完整资料，页面目前只有简短通告，无法据此判断项目资金与建设进度。')], ['项目'])
        self.assertEqual(quality['verification_status'], 'not_independently_verified')
        self.assertEqual(quality['provenance'][0]['origin_type'], 'unknown')
        self.assertIsNone(quality['independent_source_count'])

    def test_origin_url_and_tracking_parameters_link_reprint_to_original(self):
        first = row('https://a.org/original', '甲项目现场原始测试给出了详细测量记录，记录中也列明了数据采集和校准方法。')
        second = row('https://b.net/reprint', '其他网站转述甲项目测试的一项结果，没有新增原始记录，其统计范围也未明确交代。',
                     original_source='https://a.org/original?utm_source=reprint')
        details = provenance([first, second])
        self.assertEqual(details[0]['source_group'], details[1]['source_group'])
        self.assertEqual(details[1]['origin_type'], 'secondary')

    def test_same_url_opposite_or_complementary_views_are_preserved(self):
        first = row('https://a.org/story', '甲项目已获批并开始建设，文件详细介绍了许可手续和工程实施安排，所有信息仍需对照正式公告核验。')
        second = row('https://a.org/story?utm_medium=search', '甲项目尚未获批并开始建设，文件详细介绍了许可手续和工程实施安排，所有信息仍需对照正式公告核验。')
        self.assertFalse(similar(first, second))
        self.assertEqual(len(curate([first, second])), 2)
        self.assertEqual(assess([first, second], ['项目'])['unique_url_count'], 1)

    def test_numbers_and_negations_prevent_over_deduplication(self):
        original = '2025年甲项目总投资2.8亿元，包含详细设备配置与安装计划，项目还披露了未来建设安排和测试方法。'
        first = row('https://a.org/story', original)
        changed = row('https://b.net/story', original.replace('2.8', '3.2'))
        self.assertFalse(similar(first, changed))
        self.assertEqual(len(curate([first, changed])), 2)

    def test_opposing_views_without_chinese_negation_are_not_dropped(self):
        body='项目评估方支持这一施工方案，报告详细解释了对周边环境的影响和测量方法，并公布了相关原始资料供公众核对。'
        rows=[row('https://a.org/story',body),row('https://b.net/story',body.replace('支持','反对'))]
        self.assertFalse(similar(*rows))
        self.assertEqual(len(curate(rows)),2)

    def test_english_negation_is_not_lost_when_comparing_layout(self):
        body='The project is approved and the report describes the complete construction schedule, environmental impact and measurement method.'
        self.assertFalse(similar(row('https://a.org/story',body),row('https://b.net/story',body.replace('is approved','is not approved'))))

    def test_difference_after_long_common_prefix_is_not_lost(self):
        common='本次报告介绍项目的现场施工进展和测量方式。'*250
        self.assertFalse(similar(row('https://a.org/story',common+'最终项目获得批准。'),
                                 row('https://b.net/story',common+'最终项目未获得批准。')))


if __name__ == '__main__':
    unittest.main()
