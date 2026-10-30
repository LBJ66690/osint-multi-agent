"""共享执行预算与可恢复操作。人工阅读等待不占工作预算。"""
import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager


class ExecutionTimeout(TimeoutError):
    pass


class WorkBudget:
    def __init__(self, seconds, clock=time.monotonic):
        self.limit, self.clock = seconds, clock
        self.started = clock()
        self.waited = 0

    @property
    def remaining(self):
        return max(0, self.limit - (self.clock() - self.started - self.waited))

    @asynccontextmanager
    async def human_wait(self):
        start = self.clock()
        try:
            yield
        finally:
            self.waited += self.clock() - start

    async def run(self, factory, seconds, label):
        timeout = min(seconds, self.remaining)
        if timeout <= 0:
            raise ExecutionTimeout("本次执行已达到工作时间预算，已保存进度；可恢复后继续。人工等待不计入预算。")
        try:
            return await asyncio.wait_for(factory(), timeout)
        except asyncio.TimeoutError as exc:
            prefix=label.split(':')[0]
            display={'planning':'规划官','analyzing':'分析官','reasoning':'推理官','report':'报告处理','quick':'分析与结论整理','search':'网页收集'}.get(prefix,label)
            if ':check:' in label:display+='核查'
            raise ExecutionTimeout(f"{display}超过本次 {timeout:.0f} 秒等待预算，已停止请求并保留进度，可从此处恢复。") from exc


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Operations:
    def __init__(self, checkpoint, save, emit, budget):
        self.data, self.save, self.emit, self.budget = checkpoint, save, emit, budget
        self.rows = checkpoint.setdefault("operations", {})

    async def get(self, key, inputs, factory, seconds, stage, schema=None):
        digest = signature(inputs)
        cached = self.rows.get(key)
        if cached and cached.get("signature") == digest:
            value = cached["value"]
            await self.emit({"type": "operation_restored", "stage": stage, "operation": key,
                             "message": "复用已保存产出：" + key})
            return schema.model_validate(value) if schema else value
        start = time.monotonic()
        self.data["active_operation"] = key
        await self.save()
        await self.emit({"type": "operation_start", "stage": stage, "operation": key,
                         "timeout_seconds": min(seconds, self.budget.remaining)})
        value = await self.budget.run(factory, seconds, key)
        self.rows[key] = {"signature": digest, "value": value.model_dump() if hasattr(value, "model_dump") else value}
        self.data["active_operation"] = None
        await self.save()  # 核查或后续操作失败时，产出已经持久化。
        await self.emit({"type": "operation_done", "stage": stage, "operation": key,
                         "elapsed_seconds": round(time.monotonic()-start, 2)})
        return value
