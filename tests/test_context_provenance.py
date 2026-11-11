"""来源链进入模型上下文但不突破原有字符预算；全部离线。"""
import copy
import json
import unittest
from dataclasses import replace

from evidence_context import canonical_url, evidence_context
from evidence_quality import assess
from execution_policy import POLICIES
from schemas import EvidenceItem, ResearchPlan, SubQuestion


class ContextProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.plan=ResearchPlan(goal='项目',subquestions=[SubQuestion(question='项目',search_queries=['项目'])],report_types=['报告'])
        self.rows=[EvidenceItem(subquestion='项目',title='甲项目进度',source='https://a.org/first',
                                content='来源：甲通讯社\n甲项目施工已有公开记录，这些记录披露了开工时间和设备配置，内容来自同一篇原始消息。'),
                   EvidenceItem(subquestion='项目',title='甲项目转载',source='https://b.net/second',
                                content='来源：甲通讯社\n该网站转述了甲项目的开工安排，未提供新增独立采访，工程进展仍需要对照项目原始公告核验。')]

    def test_same_origin_chain_is_visible_to_model(self):
        profiles=assess(self.rows,['项目'])['provenance']
        context,stats=evidence_context(self.rows,self.plan,POLICIES['standard'],provenance_profiles=profiles)
        selected=json.loads(context)
        self.assertEqual(len(selected),2)
        self.assertEqual({r['source_group'] for r in selected},{'G1'})
        self.assertEqual({r['origin_type'] for r in selected},{'secondary'})
        self.assertEqual({r['independence_status'] for r in selected},{'shared_origin'})
        self.assertEqual({r['origin_reference'] for r in selected},{'甲通讯社'})
        self.assertEqual(stats['context_chars'],len(context))

    def test_unknown_is_not_promoted_to_verified(self):
        profiles=[{'source':self.rows[0].source,'source_group':'G1','origin_type':'unknown','independence_status':'unknown'}]
        context,_=evidence_context(self.rows[:1],self.plan,POLICIES['quick'],provenance_profiles=profiles)
        selected=json.loads(context)[0]
        self.assertEqual(selected['origin_type'],'unknown')
        self.assertEqual(selected['independence_status'],'unknown')
        self.assertNotIn('verified',selected)
        profiles[0]['independence_status']='verified'
        context,_=evidence_context(self.rows[:1],self.plan,POLICIES['quick'],provenance_profiles=profiles)
        self.assertEqual(json.loads(context)[0]['independence_status'],'unknown')

    def test_original_evidence_and_profiles_are_unchanged(self):
        profiles=assess(self.rows,['项目'])['provenance']
        before=[e.model_dump() for e in self.rows]
        original_profiles=copy.deepcopy(profiles)
        evidence_context(self.rows,self.plan,POLICIES['quick'],provenance_profiles=profiles)
        self.assertEqual([e.model_dump() for e in self.rows],before)
        self.assertEqual(profiles,original_profiles)

    def test_provenance_shares_excerpt_and_total_context_budget(self):
        self.rows[0].content+='项目工程施工安排及进度说明。'*100
        profiles=assess(self.rows,['项目'])['provenance']
        profiles[0]['origin_reference']='https://origin.example/'+('very-long-path/'*100)
        policy=replace(POLICIES['quick'],excerpt_chars=600,evidence_chars=900)
        context,stats=evidence_context(self.rows,self.plan,policy,provenance_profiles=profiles)
        self.assertGreater(len(json.loads(context)),0)
        self.assertLessEqual(len(context),policy.evidence_chars)
        self.assertEqual(stats['context_chars'],len(context))
        for r in json.loads(context):
            metadata={k:r[k] for k in ('source_group','origin_type','independence_status','origin_reference') if k in r}
            self.assertLessEqual(len(r['content'])+len(json.dumps(metadata,ensure_ascii=False)),policy.excerpt_chars)
            if 'origin_reference' in r:
                self.assertLessEqual(len(r['origin_reference']),160)
        selected=json.loads(context)[0]
        self.assertTrue(selected['origin_reference'].endswith('…'))

    def test_tiny_excerpt_keeps_content_without_metadata_overflow(self):
        profiles=assess(self.rows,['项目'])['provenance']
        policy=replace(POLICIES['quick'],excerpt_chars=30,evidence_chars=400)
        context,_=evidence_context(self.rows[:1],self.plan,policy,provenance_profiles=profiles)
        selected=json.loads(context)[0]
        self.assertNotIn('source_group',selected)
        self.assertTrue(selected['content'])
        self.assertLessEqual(len(selected['content']),30)
        self.assertLessEqual(len(context),400)

    def test_default_none_and_empty_profiles_keep_existing_output(self):
        evidence=EvidenceItem(subquestion='项目',title='项目说明',source='https://a.org/item',content='项目说明已经完整公开，用户可以对照原始文字核对资料。')
        original=evidence_context([evidence],self.plan,POLICIES['quick'])
        expected=[{'id':'E1','title':evidence.title,'source':evidence.source,'content':evidence.content,'subquestions':['项目']}]
        self.assertEqual(json.loads(original[0]),expected)
        self.assertEqual(original,evidence_context([evidence],self.plan,POLICIES['quick'],provenance_profiles=None))
        self.assertEqual(original,evidence_context([evidence],self.plan,POLICIES['quick'],provenance_profiles=[]))

    def test_profile_matching_uses_existing_url_normalization(self):
        profiles=[{'source':self.rows[0].source+'?utm_source=search','source_group':'G8','origin_type':'secondary','independence_status':'shared_origin'}]
        context,_=evidence_context(self.rows[:1],self.plan,POLICIES['quick'],provenance_profiles=profiles)
        self.assertEqual(json.loads(context)[0]['source_group'],'G8')

    def test_missing_or_invalid_profile_has_no_new_fields(self):
        expected=evidence_context(self.rows,self.plan,POLICIES['quick'])
        actual=evidence_context(self.rows,self.plan,POLICIES['quick'],provenance_profiles=[None,{}, {'source':'https://absent.org','source_group':'G1'}])
        self.assertEqual(actual,expected)

    def test_source_ids_stay_stable_after_reorder_and_new_priority_source(self):
        ids={}
        first,_=evidence_context(self.rows,self.plan,POLICIES['quick'],evidence_ids=ids)
        original={canonical_url(r['source']):r['id'] for r in json.loads(first)}
        added=EvidenceItem(subquestion='项目',title='项目官方进度',source='https://notice.gov.cn/new',
                           content='甲项目发布正式施工公告，公告包括设备配置、工期和许可编号，供公众查阅。')
        second,_=evidence_context([added,self.rows[1],self.rows[0]],self.plan,POLICIES['quick'],evidence_ids=ids)
        current={canonical_url(r['source']):r['id'] for r in json.loads(second)}
        for url,identifier in original.items():
            self.assertEqual(current[url],identifier)
        self.assertEqual(current[added.source],'E3')
        self.assertEqual(len(set(ids.values())),3)

    def test_selection_changes_and_truncation_do_not_reassign_ids(self):
        ids={}
        narrow=replace(POLICIES['quick'],evidence_items=1,excerpt_chars=25)
        first,_=evidence_context(self.rows,self.plan,narrow,evidence_ids=ids)
        first_selected=json.loads(first)[0]
        later_plan=ResearchPlan(goal='转载',subquestions=[SubQuestion(question='项目',search_queries=['转载'])],report_types=['报告'])
        second,_=evidence_context(self.rows[::-1],later_plan,narrow,evidence_ids=ids)
        second_selected=json.loads(second)[0]
        self.assertNotEqual(first_selected['source'],second_selected['source'])
        for selected in (first_selected,second_selected):
            self.assertEqual(selected['id'],ids[canonical_url(selected['source'])])
            self.assertLessEqual(len(selected['content']),25)
        third,_=evidence_context(self.rows[::-1],later_plan,POLICIES['quick'],evidence_ids=ids)
        self.assertEqual({r['source']:r['id'] for r in json.loads(third)},
                         {self.rows[0].source:'E1',self.rows[1].source:'E2'})

    def test_existing_registry_gaps_and_tracking_url_preserve_ids(self):
        ids={self.rows[0].source+'?utm_source=search':'E7'}
        context,_=evidence_context(self.rows[::-1],self.plan,POLICIES['quick'],evidence_ids=ids)
        selected={r['source']:r['id'] for r in json.loads(context)}
        self.assertEqual(selected[self.rows[0].source],'E7')
        self.assertEqual(selected[self.rows[1].source],'E8')
        self.assertEqual(ids,{self.rows[0].source:'E7',self.rows[1].source:'E8'})

    def test_invalid_or_duplicate_registry_stops_without_modifying_it(self):
        bad=[{self.rows[0].source:'E0'},{self.rows[0].source:'E01'},{self.rows[0].source:'not-an-id'},
             {self.rows[0].source:'E1',self.rows[1].source:'E1'},
             {self.rows[0].source:'E1',self.rows[0].source+'?utm_source=search':'E2'},
             {1:'E1'},{self.rows[0].source:1}]
        for registry in bad:
            with self.subTest(registry=registry):
                before=copy.deepcopy(registry)
                with self.assertRaisesRegex(ValueError,'证据编号登记表'):
                    evidence_context(self.rows,self.plan,POLICIES['quick'],evidence_ids=registry)
                self.assertEqual(registry,before)
        with self.assertRaisesRegex(ValueError,'证据编号登记表'):
            evidence_context(self.rows,self.plan,POLICIES['quick'],evidence_ids=[])

    def test_explicit_none_id_registry_preserves_default_bytes(self):
        expected=evidence_context(self.rows,self.plan,POLICIES['quick'])
        self.assertEqual(expected,evidence_context(self.rows,self.plan,POLICIES['quick'],evidence_ids=None))


if __name__=='__main__':
    unittest.main()
