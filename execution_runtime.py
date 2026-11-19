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
        task = asyncio.ensure_future(factory())
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if done:
                # 工具自身立即抛出的 TimeoutError 是工具失败，并非工作预算耗尽。
                return await task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            prefix=label.split(':')[0]
            display={'planning':'规划官','analyzing':'分析官','reasoning':'推理官','report':'报告处理','quick':'分析与结论整理','search':'网页收集'}.get(prefix,label)
            if ':check:' in label:display+='核查'
            raise ExecutionTimeout(f"{display}超过本次 {timeout:.0f} 秒等待预算，已停止请求并保留进度，可从此处恢复。")
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Operations:
    def __init__(self, checkpoint, save, emit, budget):
        self.data, self.save, self.emit, self.budget = checkpoint, save, emit, budget
        self.rows = checkpoint.setdefault("operations", {})
        # 同一操作可能被并发分支请求；串行校验缓存，避免重复付费调用。
        self._locks = {}

    async def get(self, key, inputs, factory, seconds, stage, schema=None):
        async with self._locks.setdefault(key, asyncio.Lock()):
            return await self._get(key, inputs, factory, seconds, stage, schema)

    async def _get(self, key, inputs, factory, seconds, stage, schema):
        digest = signature(inputs)
        cached = self.rows.get(key)
        if isinstance(cached, dict) and cached.get("signature") == digest:
            try:
                value = cached["value"]
                value = schema.model_validate(value) if schema else value
            except (KeyError, TypeError, ValueError):
                # 旧快照整体仍可恢复；只重新执行损坏的这一项，保留其他成功结果。
                await self.emit({"type": "operation_cache_invalid", "stage": stage, "operation": key,
                                 "message": "已保存产出不符合当前格式，仅重新执行此操作"})
            else:
                await self.emit({"type": "operation_restored", "stage": stage, "operation": key,
                                 "message": "复用已保存产出：" + key})
                return value
        start = time.monotonic()
        attempts = self.data.setdefault("operation_attempts", {})
        if not isinstance(attempts.get(key), dict):
            attempts[key] = {}
        row = attempts[key]
        for field in ("attempts", "failures", "cancellations"):
            if not isinstance(row.get(field), int) or isinstance(row.get(field), bool) or row[field] < 0:
                row[field] = 0
        row.update(attempts=row.get("attempts", 0) + 1, signature=digest, status="running", stage=stage)
        self.data["active_operation"] = key
        await self.save()
        await self.emit({"type": "operation_start", "stage": stage, "operation": key,
                         "operation_attempt": row["attempts"],
                         "timeout_seconds": min(seconds, self.budget.remaining)})
        try:
            value = await self.budget.run(factory, seconds, key)
            if schema:
                value = schema.model_validate(value)
        except asyncio.CancelledError:
            row.update(status="cancelled", cancellations=row.get("cancellations", 0) + 1,
                       elapsed_seconds=round(time.monotonic() - start, 2))
            self.data["last_failed_operation"] = key
            await self.save()
            await self.emit({"type": "operation_cancelled", "stage": stage, "operation": key,
                             "operation_attempt": row["attempts"], "elapsed_seconds": row["elapsed_seconds"],
                             "message": "操作已中断，已完成的其他产出仍可复用"})
            raise
        except Exception as exc:
            row.update(status="error", failures=row.get("failures", 0) + 1,
                       error_type=type(exc).__name__, elapsed_seconds=round(time.monotonic() - start, 2))
            self.data["last_failed_operation"] = key
            await self.save()
            await self.emit({"type": "operation_error", "stage": stage, "operation": key,
                             "operation_attempt": row["attempts"], "elapsed_seconds": row["elapsed_seconds"],
                             "error_type": type(exc).__name__, "can_resume": True})
            raise
        self.rows[key] = {"signature": digest, "value": value.model_dump() if hasattr(value, "model_dump") else value}
        row.update(status="done", elapsed_seconds=round(time.monotonic() - start, 2))
        row.pop("error_type", None)
        if self.data.get("active_operation") == key:
            self.data["active_operation"] = None
        if self.data.get("last_failed_operation") == key:
            self.data.pop("last_failed_operation", None)
        await self.save()  # 核查或后续操作失败时，产出已经持久化。
        await self.emit({"type": "operation_done", "stage": stage, "operation": key,
                         "operation_attempt": row["attempts"],
                         "elapsed_seconds": round(time.monotonic()-start, 2)})
        return value
