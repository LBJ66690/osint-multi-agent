"""局部失效标记：保留文件和操作记录，只重做输入真正变化的下游产出。"""
from execution_runtime import signature

STAGES = ['planning', 'collecting', 'analyzing', 'reasoning', 'reporting', 'verifying']
FIELDS = {'analyzing': ['knowledge'], 'reasoning': ['analysis', 'adaptive_findings'],
          'reporting': ['report_state', 'report_records', 'reports', 'verifications']}


def bind_inputs(checkpoint, stage, inputs):
    """旧快照首次登记即可兼容；之后实际输入改变，取消下游完成标记。"""
    hashes = checkpoint.setdefault('stage_inputs', {})
    digest = signature(inputs)
    previous = hashes.get(stage)
    hashes[stage] = digest
    if previous is None or previous == digest:
        return []
    affected = STAGES[STAGES.index(stage):]
    completed = checkpoint.setdefault('completed_stages', [])
    completed[:] = [s for s in completed if s not in affected]
    for name in affected:
        for field in FIELDS.get(name, []):
            checkpoint.pop(field, None)
        checkpoint.get('stage_state', {}).pop(name, None)
        if name != stage:
            hashes.pop(name, None)
    # 决策与核查的输入也包含依赖签名；保存旧操作用于诊断，不删除用户文件。
    return affected
