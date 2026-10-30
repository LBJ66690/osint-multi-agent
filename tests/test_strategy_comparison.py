import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_search_strategies import compare,markdown

class ComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_seven_actual_fixture_paths_and_honest_unknowns(self):
        d=await compare();self.assertEqual(len(d['scenarios']),7);self.assertEqual(d['paid_api_calls'],0)
        s=d['scenarios'];self.assertLess(s[1]['adaptive']['search_calls'],s[1]['legacy']['search_calls'])
        self.assertGreater(s[2]['adaptive']['supplemental_searches'],0)
        self.assertEqual(s[2]['adaptive']['subquestion_coverage'],'sufficient')
        self.assertGreater(s[3]['adaptive']['provider_switches'],0)
        self.assertLess(s[3]['adaptive']['failed_search_calls'],s[3]['legacy']['failed_search_calls'])
        self.assertTrue(any(e['action']=='VERIFY_CONFLICT' for e in s[4]['adaptive']['decisions']))
        self.assertEqual(s[4]['adaptive']['conflicts_resolved'],0)
        self.assertEqual(s[5]['adaptive']['search_calls'],0)
        self.assertEqual(s[6]['adaptive']['report_rewrites'],1)
        self.assertEqual(s[6]['adaptive']['searches_during_report_repair'],0)
        self.assertIsNone(s[6]['adaptive']['total_tokens']);self.assertIn('unknown',markdown(d))
