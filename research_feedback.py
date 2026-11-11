"""分析/推理反馈的小闭环：共享已有补搜额度，只查询用户选中的免费渠道。"""
from schemas import EvidenceItem
from evidence_context import canonical_url
from execution_runtime import signature
from adaptive_search import LIMITS


def requests_for(value, check, plan):
    questions = {q.question: q for q in plan.subquestions}
    requests, seen = [], set()
    for gap in getattr(value, 'evidence_gaps', []):
        row = gap.model_dump() if hasattr(gap, 'model_dump') else gap
        if row.get('subquestion') in questions and row['subquestion'] not in seen:
            requests.append(row)
            seen.add(row['subquestion'])
    for q in check.redo_targets:
        if q in questions and not any(r['subquestion'] == q for r in requests):
            requests.append({'subquestion': q, 'reason': check.summary, 'kind': 'missing', 'query': ''})
    return requests[:len(questions)]


async def supplement(value, check, stage, plan, selected, search, checkpoint, save, emit, budget, policy):
    """每问题最多一个补搜任务，失败仅允许在恢复时重试一次；不调用新模型。"""
    from search_sources import FREE_IDS
    jobs = checkpoint.setdefault('feedback_state', {})
    history = checkpoint.setdefault('decision_history', [])
    unresolved = checkpoint.setdefault('unresolved_gaps', [])
    additions = []
    for request in requests_for(value, check, plan):
        q = request['subquestion']
        job = jobs.get(q)
        reason = request.get('reason') or '关键结论的依据不足'
        if not any(r.get('subquestion') == q and r.get('reason') == reason for r in unresolved):
            unresolved.append({'subquestion': q, 'reason': reason, 'stage': stage, 'status': 'unconfirmed'})
        async def note(action, text):
            key = signature(['feedback', q, action, text])
            if any(e.get('key') == key for e in history):
                return
            event = {'type': 'decision', 'stage': stage, 'subquestion': q, 'action': action,
                     'reason': text, 'key': key, 'metrics': {'origin': stage}}
            history.append(event)
            await save()
            await emit(event)
        if job and job.get('status') == 'success':
            if not job.get('applied'):
                # 搜索成功已保存、尚未来得及归档就被中断：重放资料而非再请求。
                known = {(e.get('subquestion'), canonical_url(e['source']), e.get('content'))
                         for e in checkpoint.get('evidence', [])}
                additions.extend(e for e in job.get('rows', [])
                                 if (e['subquestion'], canonical_url(e['source']), e['content']) not in known)
            continue
        free = [p for p in selected if p in FREE_IDS]
        state = checkpoint.setdefault('adaptive_state', {}).setdefault(q, {})
        if not free:
            await note('STOP_SEARCH', '分析发现证据缺口；未选择免费渠道，不自动增加付费检索或模型调用。')
            continue
        if job and job.get('attempts', 0) >= 2:
            await note('STOP_SEARCH', '该问题补搜及一次恢复重试均未成功，保留缺口并停止自动请求。')
            continue
        if not job and state.get('extra', 0) >= LIMITS[policy.mode]['extra']:
            await note('STOP_SEARCH', '该问题已用完共享补搜额度，保留不确定性，不继续增加搜索。')
            continue
        if budget.remaining <= 38:
            await note('STOP_SEARCH', '剩余工作预算不足，保留时间生成报告；缺口仍未确认。')
            continue
        if job is None:
            base = request.get('query', '').strip()[:240] or q
            text = base + (' 原始资料 核实 不同说法' if request.get('kind') == 'conflict' else ' 原始资料 独立来源')
            used = {j.get('provider') for j in state.get('jobs', []) if j.get('status') == 'success'}
            provider = next((p for p in free if p not in used), free[0])
            job = jobs[q] = {'provider': provider, 'query': text, 'attempts': 0, 'reason': reason,
                            'origin_stage': stage, 'status': 'pending'}
            state['extra'] = state.get('extra', 0) + 1
        job['attempts'] += 1
        job['status'] = 'running'
        await save()
        await note('COLLECT_FEEDBACK', f'{stage} 反馈：{reason}；在共享额度内定向补搜一次，不增加模型调用。')
        sq = next(s for s in plan.subquestions if s.question == q)
        try:
            rows = await budget.run(lambda: search(job['provider'], sq, job['query']),
                                    min(30, budget.remaining - 30), 'feedback:search')
            clean = [EvidenceItem.model_validate(row).model_dump() for row in rows]
            job.update(status='success', rows=clean)
            known = {(e.get('subquestion'), canonical_url(e['source']), e.get('content'))
                     for e in checkpoint.get('evidence', [])}
            additions.extend(e for e in clean if (e['subquestion'], canonical_url(e['source']), e['content']) not in known)
        except Exception as exc:
            job.update(status='failed', error=type(exc).__name__)
            await note('FEEDBACK_FAILED', '定向补搜失败；已保留原证据和缺口，恢复时最多重试一次。')
            if budget.remaining <= 0:
                raise
        finally:
            await save()
        if job.get('status') == 'success':
            await note('FEEDBACK_RESULT', f'补搜返回 {len(job["rows"])} 条片段；尚需后续报告核查，不自动认定缺口已解决。')
    return additions
