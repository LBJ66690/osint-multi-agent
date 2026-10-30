import unittest
from unittest.mock import patch
from types import SimpleNamespace
import pipeline,server
from schemas import Knowledge,Analysis,Finding
from adaptive_search import finding_details
import test_execution as fixture_module

class ConflictRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_process_kill_and_adaptive_resume(self):
        import test_process_resume as process_tests
        code=process_tests.CHILD.replace("execution_mode='standard',","execution_mode='standard',search_strategy='adaptive',")
        code=code.replace("return [{'title':'test','content':'text','source':'https://example.com','provider':'parallel'}]",
            "return [{'title':'test','content':'This published document describes the complete project design and construction schedule.','source':'https://example.com','provider':'parallel'},{'title':'Independent study','content':'Researchers measured experimental results using a distinct technical methodology and new observations.','source':'https://second.example.net','provider':'parallel'}]")
        with patch.object(process_tests,'CHILD',code):
            test=process_tests.ProcessResumeTests()
            await test.test_killed_process_can_resume_without_redoing_completed_work()

    async def test_adaptive_failure_resume_keeps_search_and_strategy(self):
        f=fixture_module.Fixture();await f.asyncSetUp()
        try:
            f.fail_schema=Knowledge
            with self.assertRaises(ConnectionError):await f.run_task(execution_mode='standard',search_strategy='adaptive')
            before=list(f.searches);checkpoint=f.checkpoint()
            self.assertEqual(checkpoint['search_strategy'],'adaptive')
            self.assertIn('collecting',checkpoint['completed_stages']);self.assertTrue(checkpoint['decision_history'])
            f.fail_schema=None
            result=await f.run_task(resume=True,execution_mode='standard',search_strategy='legacy')
            self.assertEqual(f.searches,before);self.assertEqual(result['search_strategy'],'adaptive')
            self.assertEqual(checkpoint['decision_history'],f.checkpoint()['decision_history'])
        finally:await f.asyncTearDown()

    async def test_unknown_strategy_rejected_and_default_legacy(self):
        from pydantic import ValidationError
        self.assertEqual(server.RunRequest(task='x').search_strategy,'legacy')
        with self.assertRaises(ValidationError):server.RunRequest(task='x',search_strategy='other')

    async def test_finding_uncertainty_is_not_fake_probability(self):
        analysis=Analysis(findings=[Finding(claim='投资金额尚有不同说法',sources=['https://a.org'])])
        rows=finding_details(analysis,'[{"id":"E1","source":"https://a.org"}]',{'conflicts':[{'values':{'28000':['https://a.org'],'32000':['https://b.org']}}]})
        self.assertEqual(rows[0]['confidence'],'low');self.assertTrue(rows[0]['uncertain'])
        self.assertEqual(rows[0]['support_ids'],['E1']);self.assertTrue(rows[0]['uncertainty_reason'])
