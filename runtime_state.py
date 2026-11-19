"""事件驱动的计时快照：刷新、阶段切换及重试不重置累计时间。"""
from datetime import datetime, timezone
import time

def stamp_ms(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)

class RunTimeline:
    def __init__(self):
        self.started = None
        self.ended = None
        self.current = None
        self.active = None
        self.wait_started = None
        self.wait_total = 0
        self.wait_reason = ""
        self.paused = None
        self.stages = {}

    def _close(self, now):
        if self.active:
            row = self.stages[self.active]
            row["elapsed_ms"] += max(0, now - row["active_since_ms"])
            row["active_since_ms"] = None
            if row["status"] == "running": row["status"] = "ready"
            self.active = None

    def _end_wait(self, now):
        if self.wait_started is not None:
            self.wait_total += max(0, now - self.wait_started)
        self.wait_started, self.wait_reason = None, ""

    def consume(self, ev):
        now = stamp_ms(ev["ts"])
        if self.started is None: self.started = now
        kind = ev.get("type")
        if kind == "phase" and ev.get("stage") in self.stages:
            self.stages[ev["stage"]]["label"] = ev.get("label", "")
        if kind == "resumed":
            self.ended = None
            self.paused = None
        if kind == "stage":
            stage = ev["stage"]
            row = self.stages.setdefault(stage, {"elapsed_ms": 0, "active_since_ms": None, "status": "pending", "label": "", "model": ""})
            row.update(label=ev.get("label", ""), model=ev.get("model_label", ""))
            if ev["status"] == "running":
                self._end_wait(now)
                if self.active != stage:
                    self._close(now)
                    row["active_since_ms"] = now
                    self.active = stage
                row["status"] = "running"
                self.current = stage
            elif ev["status"] == "done":
                if self.active == stage: self._close(now)
                row["status"] = "done"
        waiting = {"confirm_required": "等待确认计划", "sources_required": "等待选择搜索 MCP"}.get(kind)
        if kind == "gate_check" and ev.get("verdict") == "fail" and ev.get("interactive"):
            waiting = "等待核查决策"
        if waiting:
            self.paused = self.active
            self._close(now)
            if self.paused: self.stages[self.paused]["status"] = "waiting"
            if self.wait_started is None: self.wait_started = now
            self.wait_reason = waiting
        if kind in {"confirm_resolved", "sources_selected", "gate_resolved"}:
            self._end_wait(now)
            if self.paused:
                self.active = self.paused
                self.stages[self.active].update(active_since_ms=now, status="running")
                self.paused = None
        if kind in {"done", "cancelled", "error"}:
            active = self.active or self.paused
            self._close(now)
            self._end_wait(now)
            if active and kind != "done": self.stages[active]["status"] = kind
            self.ended = now
            self.paused = None

    def snapshot(self):
        return {"server_ms": int(time.time() * 1000), "started_ms": self.started, "ended_ms": self.ended,
                "current_stage": self.current, "active_stage": self.active, "stages": {k: dict(v) for k, v in self.stages.items()},
                "wait_started_ms": self.wait_started, "wait_total_ms": self.wait_total, "wait_reason": self.wait_reason}
