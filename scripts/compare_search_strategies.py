"""确定性离线场景，使用真实 adaptive 收集器；无网络、无付费模型。"""
import asyncio,json,sys,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adaptive_search import collect
from evidence_quality import assess,conflicts
from execution_policy import POLICIES
from execution_runtime import WorkBudget
from schemas import ResearchPlan,SubQuestion

NAMES=['正常搜索','大量转载重复','证据不足','Provider 超时','投资金额冲突','剩余预算不足','报告引用错误局部修复']
FIELDS=['search_calls','successful_search_calls','failed_search_calls','refined_queries','supplemental_searches','early_stops',
        'raw_evidence','unique_urls','unique_domains','duplicate_ratio','subquestion_coverage','conflicts_detected','conflicts_resolved',
        'model_calls','prompt_tokens','completion_tokens','total_tokens','active_time','search_time','analysis_time','total_time',
        'failure_count','recovered_failures','provider_switches','local_retries','report_rewrites','searches_during_report_repair']

def evidence(url,text):return dict(subquestion='项目投资',source=url,title='项目建设进展',content=text,provider='fixture')
A=evidence('https://media.example.org/project','公开报道详细介绍了项目的建设内容、工程安排和此前已经公布的基本情况，提醒读者参考原始公告。')
B=evidence('https://office.example.net/notice','独立机构提供了工程实验结果与设计方案、检测条件及测量记录，具体解释项目实施中的技术标准。')

async def measure(index,strategy):
    if index==6:return await report_case(strategy)
    selected=['parallel','you'] if index not in {2} else ['parallel']
    mode='deep' if len(selected)>1 else 'standard'
    queries=['项目投资','项目建设资料'] if mode=='deep' else ['项目投资']
    plan=ResearchPlan(goal='项目投资研究',subquestions=[SubQuestion(question='项目投资',search_queries=queries)],report_types=['报告'])
    calls=[];events=[];c={};success=failed=0;start=time.perf_counter()
    async def search(provider,sq,query):
        nonlocal success,failed
        calls.append(dict(provider=provider,query=query))
        if index==3 and provider=='parallel':failed+=1;raise TimeoutError('fixture timeout')
        success+=1
        if index==1:return [dict(A),dict(B),dict(A),dict(B),dict(A)]
        if index==2:
            return [evidence('https://notice.gov.cn/project',B['content'])] if '官方' in query else [dict(A)]
        if index==4:
            return [evidence('https://a.org/invest','2025年该项目总投资2.8亿元，包含主体工程、配套建筑和相关建设费用。'),
                    evidence('https://b.org/invest','2025年该项目总投资3.2亿元，包含主体工程、配套建筑和相关建设费用。')]
        return [dict(A)] if provider=='parallel' else [dict(B)]
    async def save():pass
    async def emit(event):events.append(event)
    budget=WorkBudget(7 if index==5 else 300)
    if strategy=='adaptive':
        rows=await collect(plan,selected,search,c,save,emit,budget,POLICIES[mode])
    else:
        # 与原 one() 相同的固定来源×预设查询；本 fixture 的请求均即时返回。
        async def request(p,q):
            try:return await search(p,plan.subquestions[0],q)
            except TimeoutError:return []
        batches=await budget.run(lambda:asyncio.gather(*(request(p,q) for p in selected for q in queries)),155,'fixture legacy collecting')
        rows=[e for batch in batches for e in batch]
    elapsed=time.perf_counter()-start;q=assess(rows,['项目投资']);m=c.get('search_metrics',{})
    metrics={k:0 for k in FIELDS}
    metrics.update(search_calls=len(calls),successful_search_calls=success,failed_search_calls=failed,
        raw_evidence=len(rows),unique_urls=q['unique_url_count'],unique_domains=q['unique_domain_count'],duplicate_ratio=q['duplicate_ratio'],
        subquestion_coverage=q['coverage']['项目投资']['status'],conflicts_detected=len(conflicts(rows)),
        active_time=elapsed,search_time=elapsed,total_time=elapsed,analysis_time=None,
        failure_count=failed,model_calls=0,prompt_tokens=0,completion_tokens=0,total_tokens=0)
    for k in ['refined_queries','supplemental_searches','early_stops','provider_switches','local_retries','recovered_failures']:
        metrics[k]=m.get(k,0)
    metrics['trace']=calls;metrics['decisions']=c.get('decision_history',[])
    return metrics

async def report_case(strategy):
    # 完整执行现有编排与真正的 report_reference_check；所有外部依赖由 fixture 替代。
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
    from test_execution import Fixture
    import search_sources
    test=Fixture();await test.asyncSetUp();before=[];started=time.perf_counter()
    async def search(client,provider,query):
        test.searches.append((provider,query))
        return [{k:(v+' 本资料回答测试问题。' if k=='content' else v) for k,v in e.items() if k!='subquestion'} for e in [{**A,'source':'https://example.com/source'},B]]
    async def report(*args,**kw):
        test.calls['report']+=1;before.append(len(test.searches))
        return ('# 错误引用 [来源](https://unknown.example/wrong)' if test.calls['report']==1
                else '# 修订后 [来源](https://example.com/source)')
    async def gate(payload):return {'decision':'redo' if payload['verdict']=='fail' else 'continue','feedback':''}
    try:
        with patch.object(search_sources.MCPWebSearchClient,'search',new=search),patch('pipeline.generate_report',side_effect=report):
            await test.run_task(execution_mode='standard',search_strategy=strategy,gate=gate)
        metrics={k:0 for k in FIELDS};elapsed=time.perf_counter()-started
        metrics.update(search_calls=len(test.searches),successful_search_calls=len(test.searches),raw_evidence=len(test.searches)*2,unique_urls=2,unique_domains=2,
            subquestion_coverage='sufficient',model_calls=sum(v for k,v in test.calls.items() if k!='report')+test.calls['report'],
            prompt_tokens=None,completion_tokens=None,total_tokens=None,active_time=elapsed,total_time=elapsed,
            search_time=None,analysis_time=None,report_rewrites=test.calls['report']-1,searches_during_report_repair=len(test.searches)-before[0],
            trace=[{'provider':p,'query':q} for p,q in test.searches],decisions=[])
        assert metrics['searches_during_report_repair']==0 and metrics['report_rewrites']==1
        return metrics
    finally:await test.asyncTearDown()

async def compare():
    scenes=[]
    for i,name in enumerate(NAMES):
        scenes.append({'name':name,'legacy':await measure(i,'legacy'),'adaptive':await measure(i,'adaptive')})
    return {'fixture':True,'paid_api_calls':0,'notes':'固定模拟搜索；时间为本地模拟执行时间，不代表网络性能；报告场景的 model_calls 是模拟调用，Token 未测则 unknown。冲突数量为潜在数字差异，并未自动核实。','scenarios':scenes}

LABELS={'search_calls':'搜索次数','successful_search_calls':'成功查询','failed_search_calls':'失败查询','refined_queries':'改词次数','supplemental_searches':'补搜次数','early_stops':'提前停止','raw_evidence':'原始资料','unique_urls':'独立网址','unique_domains':'来源域名','duplicate_ratio':'内容重复率','subquestion_coverage':'子问题覆盖','conflicts_detected':'潜在金额差异','conflicts_resolved':'已核实解决冲突','model_calls':'模拟模型调用','prompt_tokens':'输入 Token','completion_tokens':'输出 Token','total_tokens':'总 Token','active_time':'模拟工作秒数','search_time':'模拟检索秒数','analysis_time':'分析秒数','total_time':'总秒数','failure_count':'故障数','recovered_failures':'通过其他成功结果恢复','provider_switches':'已选渠道切换','local_retries':'单次查询重试','report_rewrites':'报告重写','searches_during_report_repair':'报告修复额外搜索'}

def markdown(data):
    lines=['# Legacy vs Adaptive','',data['notes'],'','各场景在两种策略中使用相同任务与模拟来源。搜索场景为项目投资研究，报告场景为固定编排验收任务。无付费 API；对比没有改变或读取真实研究任务。']
    def display(v):return 'unknown' if v is None else f'{v:.5f}' if isinstance(v,float) else str(v)
    for s in data['scenarios']:
        lines+=['','## '+s['name'],'','| 指标 | Legacy | Adaptive | 变化 |','|---|---:|---:|---:|']
        for k in FIELDS:
            a,b=s['legacy'][k],s['adaptive'][k]
            diff=(f'{(b-a)*100:+.1f}pp' if k=='duplicate_ratio' else f'{b-a:+.5f}' if isinstance(a,float) else f'{b-a:+}') if isinstance(a,(int,float)) and isinstance(b,(int,float)) else '—'
            lines.append(f'| {LABELS.get(k,k)} ({k}) | {display(a)} | {display(b)} | {diff} |')
        lines+=['','### 修改前的实际查询']+[f"- {v['provider']}：{v['query']}" for v in s['legacy']['trace']]
        lines+=['','### Adaptive 做出了哪些额外决策？']+[f"- {v['subquestion']}：**{v['action']}** — {v['reason']}" for v in s['adaptive']['decisions']]
        if not s['adaptive']['decisions']:lines+=['- 未增加检索动作；报告引用场景仅修订当前报告，不补搜。']
    return '\n'.join(lines)+'\n'

async def main():
    data=await compare();path=Path(__file__).resolve().parents[1]/'outputs/comparison';path.mkdir(parents=True,exist_ok=True)
    (path/'latest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    (path/'latest.md').write_text(markdown(data),encoding='utf-8')
    print('Generated outputs/comparison/latest.json and latest.md; paid API calls: 0')
if __name__=='__main__':asyncio.run(main())
