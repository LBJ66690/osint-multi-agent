"""恢复前校验快照并给出将复用的阶段，防止损坏快照被当成新任务。"""
from schemas import ResearchPlan, EvidenceItem, Knowledge, Analysis

STAGES = ['planning','collecting','analyzing','reasoning','reporting','verifying']
LABELS = dict(zip(STAGES,['规划','收集','分析','推理','报告','核查']))

def resume_info(data, run_id=None):
    if not isinstance(data,dict) or not isinstance(data.get('task'),str) or not data['task'].strip():
        raise ValueError('检查点缺少任务信息')
    if run_id and data.get('run_id') != run_id:
        raise ValueError('检查点与当前任务不匹配')
    completed=data.get('completed_stages',[])
    if not isinstance(completed,list) or any(s not in STAGES for s in completed):
        raise ValueError('检查点的阶段记录无效')
    for stage,field,schema in [('planning','plan',ResearchPlan),('analyzing','knowledge',Knowledge),('reasoning','analysis',Analysis)]:
        if stage in completed:
            if field not in data:
                raise ValueError(f'已完成的{LABELS[stage]}缺少产出，无法安全恢复')
            schema.model_validate(data[field])
    if 'collecting' in completed:
        if not isinstance(data.get('evidence'),list) or not data['evidence']:
            raise ValueError('已完成收集但证据缺失')
        for row in data['evidence']:EvidenceItem.model_validate(row)
    for stage in completed:
        if stage in {'reporting','verifying'}:continue
        if any(prev not in completed for prev in STAGES[:STAGES.index(stage)]):
            raise ValueError('检查点阶段顺序不完整')
    if not isinstance(data.get('operations',{}),dict):
        raise ValueError('检查点操作记录无效')
    # 不校验每条操作的业务内容：损坏的缓存由 Operations 局部重算，不能因此丢掉整项任务。
    for field in ('stage_state', 'decisions', 'confirmations', 'report_state', 'operation_attempts'):
        if field in data and not isinstance(data[field], dict):
            raise ValueError(f'检查点的 {field} 记录无效')
    roles = data.get('role_models', {})
    if not isinstance(roles, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in roles.items()):
        raise ValueError('检查点的岗位模型记录无效')
    active = data.get('active_operation')
    if active is not None and not isinstance(active, str):
        raise ValueError('检查点的进行中操作记录无效')
    next_stage=next((s for s in STAGES if s not in completed),'verifying')
    return {'completed_stages':completed,'reuse_labels':[LABELS[s] for s in completed],
            'next_stage':next_stage,'next_label':LABELS[next_stage],
            'active_operation':active, 'role_models':roles,
            'last_failed_operation':data.get('last_failed_operation'),
            'last_error':data.get('last_error')}
