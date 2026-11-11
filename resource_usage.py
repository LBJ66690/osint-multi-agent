"""从已有事件统计实际请求；缺失的供应商 Token 数据保留为未知，不估算费用。"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite


def _role(event):
    if event.get('role'):
        return event['role']
    operation = event.get('operation', '')
    if ':check:' in operation:
        return 'verifier'
    return {'planning': 'planner', 'collecting': 'collector', 'analyzing': 'analyst',
            'reasoning': 'reasoner', 'verifying': 'verifier', 'reporting': 'reporter',
            'report': 'reporter', 'quick': 'analyst'}.get(operation.split(':')[0],
            {'planning': 'planner', 'collecting': 'collector', 'analyzing': 'analyst',
             'reasoning': 'reasoner', 'reporting': 'reporter', 'verifying': 'verifier'}.get(event.get('stage'), 'unknown'))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        return None
    return int(value)


def _usage(event):
    usage = event.get('usage') or {}
    if not isinstance(usage, dict):
        usage = {}
    return {key: _number(usage.get(key)) for key in ('prompt', 'completion', 'total')}


def consume_event(stats, event):
    """就地更新可持久化 dict。重复的 request_id 事件重放不增加计数。

    只把 model_request 当实际模型请求，operation_start 是逻辑工作单位。
    调用方应将 stats 放进检查点；历史恢复时重放同一批事件也安全。
    """
    stats.setdefault('version', 1)
    models = stats.setdefault('model_requests', {})
    searches = stats.setdefault('search_requests', {})
    kind = event.get('type')
    request_id = event.get('request_id')
    if kind in {'model_request', 'model_response', 'model_error', 'model_parse_error'} and request_id:
        row = models.setdefault(str(request_id), {'usage': {'prompt': None, 'completion': None, 'total': None}})
        row.update(role=event.get('role') or row.get('role') or _role(event),
                   model=event.get('model') or row.get('model') or None,
                   operation=event.get('operation') or row.get('operation') or '',
                   attempt=event.get('attempt', row.get('attempt', 1)))
        if kind == 'model_request':
            row['started'] = True
            row.setdefault('status', 'running')
        elif kind == 'model_response':
            row.update(started=True, status='returned', usage=_usage(event))
            if event.get('elapsed_seconds') is not None:
                row['elapsed_seconds'] = event['elapsed_seconds']
        elif kind == 'model_parse_error':
            row['parse_error'] = True
        else:
            row.update(started=True, status='cancelled' if event.get('cancelled') else 'error')
    # 不聚合旧的 usage 汇总：新事件已经逐请求统计，再算一次会重复计费。
    if kind in {'tool_call', 'tool_result', 'tool_error'} and event.get('stage') == 'collecting':
        call_id = event.get('call_id')
        if not call_id and event.get('call_index') is not None:
            call_id = f"{event.get('tool')}:{event['call_index']}:{event.get('run_attempt', '')}"
        # 搜索上限拒绝事件没有 call_id，代表没有发出请求，不可算成调用。
        if call_id:
            row = searches.setdefault(str(call_id), {})
            row.update(provider=event.get('tool') or row.get('provider'),
                       supplemental=bool(event.get('supplemental') or row.get('supplemental')),
                       retry=bool(event.get('retry') or row.get('retry')))
            if kind == 'tool_call':
                row['started'] = True
                row.setdefault('status', 'running')
            elif kind == 'tool_result':
                row.update(started=True, status='returned')
            elif row.get('started'):
                row['status'] = 'error'
    if kind == 'operation_restored':
        restored = stats.setdefault('restored_operations', [])
        key = event.get('operation')
        if key and key not in restored:
            restored.append(key)
    if kind in {'operation_error', 'operation_cancelled'}:
        key = f"{event.get('operation')}:{event.get('operation_attempt', '')}"
        stats.setdefault('operation_failures', {})[key] = kind
    if kind == 'operation_start' and isinstance(event.get('operation_attempt'), int) and event['operation_attempt'] > 1:
        key = f"{event.get('operation')}:{event['operation_attempt']}"
        stats.setdefault('operation_retries', {})[key] = True
    if (kind == 'decision' and event.get('action') == 'STOP_SEARCH') or kind == 'resource_stop':
        reason = event.get('reason') or event.get('message')
        reasons = stats.setdefault('stop_reasons', [])
        if reason and reason not in reasons:
            reasons.append(str(reason)[:500])
    return stats


def _model_totals(rows, history_incomplete=False):
    calls = [row for row in rows if row.get('started')]
    result = {'model_calls': len(calls),
              'model_failures': sum(row.get('status') == 'error' for row in calls),
              'model_cancellations': sum(row.get('status') == 'cancelled' for row in calls),
              'parse_failures': sum(bool(row.get('parse_error')) for row in calls),
              'model_retries': sum(isinstance(row.get('attempt'), int) and row['attempt'] > 1 for row in calls)}
    for field in ('prompt', 'completion', 'total'):
        values = [row.get('usage', {}).get(field) for row in calls]
        known = [_number(value) for value in values if _number(value) is not None]
        result['known_' + field] = sum(known)
        result[field] = sum(known) if calls and len(known) == len(calls) and not history_incomplete else None
    result['unknown_usage_calls'] = sum(row.get('usage', {}).get('total') is None for row in calls)
    return result


def snapshot(stats):
    """供 API/看板使用的统计快照。known_* 为已知部分，不能当总量。"""
    models = list((stats.get('model_requests') or {}).values())
    searches = [row for row in (stats.get('search_requests') or {}).values() if row.get('started')]
    incomplete = bool(stats.get('history_incomplete'))
    roles = {role: _model_totals([row for row in models if row.get('role') == role], incomplete)
             for role in sorted({row.get('role', 'unknown') for row in models})}
    result = _model_totals(models, incomplete)
    result.update(roles=roles, search_calls=len(searches),
                  search_failures=sum(row.get('status') == 'error' for row in searches),
                  supplemental_searches=sum(bool(row.get('supplemental')) for row in searches),
                  search_retries=sum(bool(row.get('retry')) for row in searches),
                  restored_operations=len(stats.get('restored_operations') or []),
                  operation_failures=len(stats.get('operation_failures') or {}),
                  operation_retries=len(stats.get('operation_retries') or {}),
                  stop_reasons=list(stats.get('stop_reasons') or []),
                  usage_available=result['total'] is not None,
                  history_incomplete=incomplete,
                  scope='recorded_requests',
                  price=None)
    # 统计数据均来自事件，不含问题/报告/Key；返回独立对象，避免前端/调用方改写状态。
    return deepcopy(result)
