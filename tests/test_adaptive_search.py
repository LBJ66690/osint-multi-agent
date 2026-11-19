import asyncio,unittest
from types import SimpleNamespace
from adaptive_search import collect,LIMITS
from execution_runtime import WorkBudget
from execution_policy import POLICIES
from schemas import ResearchPlan,SubQuestion
from test_evidence_quality import row

class AdaptiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_subquestions_keep_parallel_search(self):
        plan=ResearchPlan(goal='项目',subquestions=[SubQuestion(question=q,search_queries=[q]) for q in ['项目甲','项目乙']],report_types=['报告'])
        started=set();both=asyncio.Event()
        async def search(p,s,q):
            started.add(s.question)
            if len(started)==2:both.set()
            await asyncio.wait_for(both.wait(),.5)
            return [row(q=s.question)]
        async def noop(*args):pass
        await collect(plan,['parallel'],search,{},noop,noop,WorkBudget(30),POLICIES['quick'])
        self.assertEqual(started,{'项目甲','项目乙'})

    async def run_search(self,fn,queries=None,selected=None,ckpt=None,budget=None,mode='standard'):
        plan=ResearchPlan(goal='项目',subquestions=[SubQuestion(question='项目',search_queries=queries or ['项目投资'])],report_types=['报告'])
        checkpoint={} if ckpt is None else ckpt;events=[]
        async def save():pass
        async def emit(e):events.append(e)
        result=await collect(plan,selected or ['parallel'],fn,checkpoint,save,emit,budget or WorkBudget(300),POLICIES[mode])
        return result,checkpoint,events

    async def test_supplement_and_refine_are_bounded(self):
        calls=[]
        async def search(p,s,q):calls.append(q);return [row()]
        rows,c,e=await self.run_search(search)
        self.assertLessEqual(len(calls),3)
        self.assertIn('ADD_SEARCH',[v['action'] for v in e if v['type']=='decision'])
        self.assertIn('REFINE_QUERY',[v['action'] for v in e if v['type']=='decision'])

    async def test_high_repetition_stops_pending_queries(self):
        calls=[]
        async def search(p,s,q):calls.append((p,q));return [row(),row('https://b.org','另一机构独立披露了工程试验记录及设计方法，说明项目建设实施进度和实际测量结果。')]
        _,c,e=await self.run_search(search,['q1','q2'],['parallel','you','bailian'],mode='deep')
        self.assertEqual(len(calls),2);self.assertEqual(c['search_metrics']['early_stops'],1)

    async def test_fallback_only_uses_selected_providers(self):
        calls=[]
        async def search(p,s,q):
            calls.append(p)
            if p=='parallel':raise TimeoutError('simulated')
            return [row()]
        _,c,e=await self.run_search(search,selected=['parallel','you'])
        self.assertEqual(set(calls),{'parallel','you'});self.assertNotIn('bailian',calls)
        self.assertEqual(c['search_metrics']['provider_switches'],1)

    async def test_budget_reserve_stops_without_calling(self):
        async def search(*args):raise AssertionError('must not search')
        _,c,e=await self.run_search(search,budget=WorkBudget(7))
        self.assertIn('budget constrained',e[0]['reason'])

    async def test_resume_reuses_finished_queries(self):
        calls=[];checkpoint={};cancel=[True]
        async def search(p,s,q):
            calls.append(q)
            if len(calls)==2 and cancel[0]:raise asyncio.CancelledError()
            return [row()]
        with self.assertRaises(asyncio.CancelledError):await self.run_search(search,ckpt=checkpoint)
        first=calls[0];cancel[0]=False
        await self.run_search(search,ckpt=checkpoint)
        self.assertEqual(calls.count(first),1)

    async def test_conflict_triggers_one_directed_search_and_stays_uncertain(self):
        calls=[]
        async def search(p,s,q):
            calls.append(q)
            return [row(content='2025年该项目总投资2.8亿元，包含一期建筑和建设配套设施。'),row('https://b.org','2025年该项目总投资3.2亿元，包含一期建筑和建设配套设施。')]
        _,c,e=await self.run_search(search)
        self.assertTrue(any('官方' in q for q in calls));self.assertEqual(len(c['conflicts']),1)
        self.assertTrue(any(v.get('action')=='VERIFY_CONFLICT' for v in e))

    async def test_all_failed_can_retry_only_failed_jobs(self):
        ok=[False];calls=[];c={}
        async def search(p,s,q):
            calls.append(q)
            if not ok[0]:raise TimeoutError()
            return [row()]
        await self.run_search(search,ckpt=c);ok[0]=True
        await self.run_search(search,ckpt=c)
        self.assertGreater(c['search_metrics']['local_retries'],0)
