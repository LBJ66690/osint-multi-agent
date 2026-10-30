import unittest
from evidence_quality import assess,information_gain,conflicts,domain,curate
from specialized_sources import source_quality

def row(url='https://a.example.org/a',content=None,q='项目'):
    return dict(subquestion=q,source=url,title='项目建设公告',content=content or '这是项目建设的一份完整公开资料，详细介绍了项目内容和发布机构。')

class QualityTests(unittest.TestCase):
    def test_curated_context_preserves_archive_and_prefers_primary_copy(self):
        rows=[row('https://blog.org/a'),row('https://notice.gov.cn/a')]
        result=curate(rows)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['source'],'https://notice.gov.cn/a')
        self.assertEqual(len(rows),2)
    def test_copy_across_urls_is_not_independent_evidence(self):
        a=row();b=row('https://b.example.net/b')
        self.assertEqual(assess([a,b],['项目'])['coverage']['项目']['status'],'weak')
        self.assertEqual(information_gain([a],[b])['level'],'low')
    def test_domain_and_tracking(self):
        self.assertEqual(domain('https://news.qq.com/a'),domain('https://www.qq.com/b'))
        self.assertEqual(assess([row('https://a.org/x?utm_a=1'),row('https://a.org/x')],['项目'])['unique_url_count'],1)
    def test_diverse_nonidentical_sources(self):
        b=row('https://b.org/x','研究机构发布新的技术数据，其中披露了工程测试的方法及不同条件下的独立实验结果。')
        self.assertEqual(assess([row(),b],['项目'])['coverage']['项目']['status'],'sufficient')
        self.assertEqual(information_gain([row()],[b])['level'],'high')
    def test_missing_and_invalid(self):
        self.assertEqual(assess([row('javascript:bad')],['项目'])['coverage']['项目']['status'],'insufficient')
    def test_conflict_not_mixed_across_years(self):
        a=row(content='2025年该项目总投资2.8亿元，包含一期建筑和建设配套设施。')
        b=row('https://b.org', '2026年该项目总投资3.2亿元，包含二期建筑和建设配套设施。')
        self.assertFalse(conflicts([a,b])); b['content']=b['content'].replace('2026','2025')
        self.assertEqual(len(conflicts([a,b])),1)
    def test_authority_suffix_not_substring(self):
        self.assertEqual(source_quality('https://x.gov.cn/a')['level'],'primary')
        self.assertEqual(source_quality('https://gov.cn.evil.com/a')['level'],'ordinary')
