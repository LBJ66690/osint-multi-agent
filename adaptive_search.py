"""反馈层：只在 collecting 内调整检索；状态与决定均可恢复，不调用额外 LLM。"""
import time
import asyncio
from execution_runtime import signature
from evidence_quality import assess,information_gain,conflicts,record
from specialized_sources import refine_query

LIMITS={'quick':dict(extra=1,refine=1,conflict=1,switch=1),
        'standard':dict(extra=2,refine=2,conflict=1,switch=2),
        'deep':dict(extra=3,refine=2,conflict=2,switch=3)}

async def collect(plan,selected,search,checkpoint,save,emit,budget,policy):
    limits=LIMITS[policy.mode]; root=checkpoint.setdefault('adaptive_state',{})
    history=checkpoint.setdefault('decision_history',[])
    async def decision(q,action,reason,metrics):
        key=signature([q,action,reason,metrics])
        if any(x.get('key')==key for x in history):return
        event={'type':'decision','stage':'collecting','subquestion':q,'action':action,'reason':reason,'metrics':metrics,'key':key}
        history.append(event);await save();await emit(event)
    allrows=[]
    async def one(sq):
        q=sq.question
        state=root.setdefault(q,{'jobs':[],'rows':[],'extra':0,'refine':0,'conflict':0,'switch':0,'low':0,'done':False})
        rows=state['rows']
        if state['done']:
            return rows
        if not state['jobs']:
            # 每次先观察一个来源；后续失败只切换已授权来源。
            state['jobs']=[{'provider':p,'query':t,'kind':'planned'} for t in (sq.search_queries[:policy.queries] or [q]) for p in selected]
            await save()
        for job in state['jobs']:
            if job.get('status') in {'success','skipped'}:continue
            if any(j.get('status')=='failed' and j['provider']==job['provider'] for j in state['jobs']) and any(j.get('status')=='success' and j['provider']!=job['provider'] for j in state['jobs']):
                job['status']='skipped';await save();continue
            if job.get('status')=='failed':
                # 已由其他渠道恢复的失败不再重复；完全失败恢复时仅重试失败请求。
                if rows:continue
                await decision(q,'RETRY_QUERY','恢复未成功的单次查询，不重新执行成功搜索',{})
            if budget.remaining<8:
                await decision(q,'STOP_SEARCH','budget constrained：保留时间给分析与报告，当前证据可能不足',assess(rows,[q]))
                state['stop_reason']='budget constrained';break
            before=list(rows);start=time.monotonic()
            counts=checkpoint.setdefault('search_metrics',dict(search_calls=0,successful_search_calls=0,failed_search_calls=0,
                       refined_queries=0,supplemental_searches=0,early_stops=0,failure_count=0,recovered_failures=0,
                       provider_switches=0,local_retries=0,search_time=0))
            counts['search_calls']+=1
            if job.get('status')=='failed':counts['local_retries']+=1
            await save()
            try:
                result=await budget.run(lambda:search(job['provider'],sq,job['query']),60,'adaptive:search')
            except Exception as exc:
                if budget.remaining<=0:raise
                job['status']='failed';job['error']=type(exc).__name__
                counts['failed_search_calls']+=1;counts['failure_count']+=1
                if state['switch']<limits['switch']:
                    other=next((p for p in selected if p!=job['provider']),None)
                    if other:
                        state['switch']+=1;counts['provider_switches']+=1
                        await decision(q,'SWITCH_SOURCE',f"{job['provider']} 失败；尝试已选择的 {other}",{'selected':selected})
                await save();continue
            finally:
                counts['search_time']+=time.monotonic()-start
            job['status']='success';counts['successful_search_calls']+=1
            if any(j.get('status')=='failed' for j in state['jobs']):counts['recovered_failures']=min(counts['failure_count'],counts['recovered_failures']+1)
            rows.extend(map(record,result));gain=information_gain(before,result);quality=assess(rows,[q]);coverage=quality['coverage'][q]['status']
            state['low']=state['low']+1 if gain['level']=='low' else 0
            state['last_gain']=gain
            await save()
            await emit({'type':'evidence_quality','stage':'collecting','metrics':quality})
            pending=[j for j in state['jobs'] if 'status' not in j]
            found=conflicts(rows)
            if found and state['conflict']<limits['conflict'] and state['extra']<limits['extra']:
                text=refine_query(q,sq.search_queries[0] if sq.search_queries else q,conflict=True)
                if not any(j['query']==text for j in state['jobs']):
                    state['conflict']+=1;state['extra']+=1;counts['supplemental_searches']+=1
                    state['jobs'].insert(state['jobs'].index(job)+1,{'provider':job['provider'],'query':text,'kind':'conflict'})
                    await decision(q,'VERIFY_CONFLICT','发现同一问题的投资金额差异，定向查找官方口径；暂不裁决',{'conflicts':len(found)})
                    await save();continue
            if state['low']>=2 or (coverage=='sufficient' and gain['level']=='low'):
                counts['early_stops']+=1
                await decision(q,'STOP_SEARCH','连续低收益或来源已够且本次无非重复内容；弱覆盖仍会标为证据缺口',{'coverage':coverage,'information_gain':gain})
                state['stop_reason']='low gain';break
            if coverage!='sufficient' and not pending and state['extra']<limits['extra']:
                repeat=gain['level']=='low'; action='REFINE_QUERY' if repeat else 'ADD_SEARCH'
                if repeat and state['refine']>=limits['refine']:break
                text=refine_query(q,sq.search_queries[0] if sq.search_queries else q,state['extra'])
                if not any(j['query']==text for j in state['jobs']):
                    state['extra']+=1;state['refine']+=int(repeat)
                    counts['supplemental_searches']+=1;counts['refined_queries']+=int(repeat)
                    state['jobs'].append({'provider':job['provider'],'query':text,'kind':action})
                    await decision(q,action,'独立来源不足；优先用规则查找原始/官方资料',{'coverage':coverage,'information_gain':gain})
            await save()
        state['done']=bool(rows)  # 完全失败保持可重试；不把缺资料伪装成完成。
        await save()
        await emit({'type':'collection_progress','subquestion':q,'count':len(rows)})
        return rows
    batches=await asyncio.gather(*(one(sq) for sq in plan.subquestions))
    allrows=[e for batch in batches for e in batch]
    checkpoint['evidence_metrics']=assess(allrows,[s.question for s in plan.subquestions])
    checkpoint['conflicts']=conflicts(allrows)
    await save()
    return allrows

def limitations(checkpoint):
    metrics=checkpoint.get('evidence_metrics',{})
    gaps=[q for q,v in metrics.get('coverage',{}).items() if v['status']!='sufficient']
    conflict=checkpoint.get('conflicts',[])
    return ('\n\n## 检索范围与不确定性\n'
            +'\n'.join('- 证据不足或来源单一：'+q for q in gaps)
            + ('\n- 公开资料的投资金额存在不同说法，目前无法根据现有证据确认；可能涉及不同口径。' if conflict else '')
            + '\n- 来源多样性和域名规则仅用于检索排序，不代表事实正确率。')

def finding_details(analysis,context,checkpoint):
    import json
    from evidence_context import canonical_url
    ids={canonical_url(e['source']):e['id'] for e in json.loads(context)}
    conflicted={canonical_url(u) for c in checkpoint.get('conflicts',[]) for urls in c['values'].values() for u in urls}
    results=[]
    for finding in analysis.findings:
        row=finding.model_dump();urls={canonical_url(u) for u in finding.sources}
        unsure=bool(urls&conflicted)
        row.update(support_ids=[ids[u] for u in ids if u in urls],counter_ids=[],
                   confidence='low' if unsure else 'medium',uncertain=unsure,
                   uncertainty_reason='涉及公开资料的金额差异，尚不能确定口径' if unsure else '来源匹配不代表独立事实认证')
        results.append(row)
    return results
