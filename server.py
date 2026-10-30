# server.py -- 开源情报多智能体平台的 Web 服务
# FastAPI + SSE：驱动 pipeline.py，支持断点续传、内存LRU管理、历史恢复与全链路日志
from __future__ import annotations

import asyncio
import json
import uuid
import time
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal
from urllib.parse import quote

import config
import models
import pipeline
import search_sources
from runtime_state import RunTimeline
from execution_policy import choose_policy
from checkpoint_resume import resume_info

ROOT = config.ROOT
STATIC = ROOT / "static"

@asynccontextmanager
async def lifespan(app):
    from asyncio_compat import install_cleanup_handler
    restore = install_cleanup_handler(asyncio.get_running_loop())
    try:
        yield
    finally:
        restore()


app = FastAPI(title="开源情报多智能体平台", version="2.5", lifespan=lifespan)

FINAL = {"done", "error", "cancelled"}
MAX_RUNS = 60  # 内存中最大保留任务数，避免长时间运行 OOM


class RunState:
    def __init__(self, task: str, model_id: str, auto_confirm: bool = False,
                 role_models: Optional[dict] = None, execution_mode: str = "auto"):
        self.id = uuid.uuid4().hex[:12]
        self.task = task
        self.model_id = model_id
        self.role_models = role_models or models.role_defaults(choose_policy(task, execution_mode).mode)
        self.execution_mode = execution_mode
        self.status = "pending"  # pending / running / awaiting_confirm / awaiting_gate / done / error / cancelled
        self.events: list[dict] = []
        self.timeline = RunTimeline()
        self.persist_lock = asyncio.Lock()
        self.latest_plan: dict | None = None
        self.cond = asyncio.Condition()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # 启动预热等无事件循环场景（如从磁盘恢复历史）
        self.confirm_future: asyncio.Future = loop.create_future() if loop else None
        self.gate_future: asyncio.Future = loop.create_future() if loop else None
        self.pending_gate: Optional[dict] = None  # 当前等待用户决策的关卡信息
        self.sources_future: asyncio.Future | None = None
        self.pending_sources: dict | None = None
        self.search_sources: list[str] = []
        self.auto_confirm = auto_confirm
        self.summary: dict = {}
        self.reports: list[dict] = []
        self.task_obj: Optional[asyncio.Task] = None
        self.created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.updated_at = self.created_at

    def ensure_future(self):
        if self.confirm_future is None or self.confirm_future.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self.confirm_future = loop.create_future()

    def ensure_gate_future(self):
        if self.gate_future is None or self.gate_future.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self.gate_future = loop.create_future()

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "task": self.task,
            "model_id": self.model_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "report_count": len(self.reports),
            "execution_mode": self.execution_mode,
        }

    def to_detail(self) -> dict:
        recovery=None
        if self.status in {'error','cancelled'}:
            try:
                recovery=resume_info(pipeline.load_checkpoint(self.id),self.id)
            except (ValueError,TypeError,KeyError):
                recovery={'unavailable':True,'message':'检查点缺失或不完整，无法安全恢复'}
        return {
            **self.to_summary(),
            "events": self.events,
            "summary": self.summary,
            "reports": self.reports,
            "pending_gate": self.pending_gate,
            "pending_sources": self.pending_sources,
            "search_sources": self.search_sources,
            "timeline": self.timeline.snapshot(),
            "pending_plan": self.latest_plan if self.status == "awaiting_confirm" else None,
            "role_models": self.role_models,
            "resume_info": recovery,
        }


runs: dict[str, RunState] = {}


def _add_run(state: RunState):
    """内存任务维护与 LRU 淘汰"""
    if len(runs) >= MAX_RUNS:
        candidates = [k for k, s in runs.items() if s.status in FINAL]
        if not candidates:
            raise HTTPException(status_code=429,detail='运行中的任务已达上限，请先结束一个任务后重试')
        if candidates:
            oldest = min(candidates, key=lambda k: runs[k].created_at)
            config.logger.info(f"LRU 淘汰历史内存任务: {oldest}")
            runs.pop(oldest, None)
    runs[state.id] = state


def _events_path(run_id: str) -> Path:
    return config.EVENTS_DIR / f"events_{run_id}.json"


async def _persist_events(state: "RunState") -> None:
    """将运行过程事件流落盘，服务重启后即可完整回放历史。"""
    if not state.id or not state.events:
        return
    try:
        p = _events_path(state.id)
        async with state.persist_lock:
            snapshot = json.dumps(state.events, ensure_ascii=False)
            temporary = p.with_suffix(".tmp")
            await asyncio.to_thread(temporary.write_text, snapshot, encoding="utf-8")
            await asyncio.to_thread(temporary.replace, p)
    except Exception as e:
        config.logger.warning(f"[{state.id}] 事件流落盘异常: {e}")


def _load_events(run_id: str) -> list[dict]:
    """从磁盘读取历史事件流；无文件或损坏时返回空列表（走 summary 兜底）。"""
    try:
        p = _events_path(run_id)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception as e:
        config.logger.warning(f"[{run_id}] 读取历史事件流失败: {e}")
    return []


def _restore_history_from_disk():
    """服务启动时扫描已落盘的检查点，恢复历史任务列表"""
    try:
        ckpts = sorted(config.CHECKPOINT_DIR.glob("ckpt_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in ckpts[:MAX_RUNS]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                rid = data.get("run_id")
                if rid and rid not in runs:
                    st = RunState(
                        task=data.get("task", ""),
                        model_id=data.get("model_id", ""),
                        role_models=data.get("role_models"),
                        execution_mode=data.get("execution_mode", "auto"),
                    )
                    st.id = rid
                    st.search_sources = data.get("search_sources", [])
                    is_done = data.get("status") == "done" if data.get("schema_version") == 2 else "verifying" in data.get("completed_stages", [])
                    st.status = "done" if is_done else "error"
                    st.reports = [
                        {"type": rt, "path": p, "markdown": _read_report(p)}
                        for rt, p in _iter_reports(data)
                    ]
                    st.summary = {
                        "reports": data.get("reports", []),
                        "plan": data.get("plan"),
                        "evidence": data.get("evidence", []),
                        "knowledge": data.get("knowledge"),
                        "findings": data.get("analysis", {}).get("findings", []),
                        "verifications": data.get("verifications", {}),
                    }
                    st.events = _load_events(rid)
                    for ev in st.events:
                        if ev.get("ts"):
                            st.timeline.consume(ev)
                        if ev.get("type") == "plan": st.latest_plan = ev.get("plan")
                    if st.events:
                        st.created_at = st.events[0].get("ts", st.created_at)
                        st.updated_at = st.events[-1].get("ts", st.updated_at)
                        if st.timeline.ended is None:
                            st.timeline.consume({"type": "error", "ts": st.updated_at})
                    runs[rid] = st
            except Exception as e:
                config.logger.warning(f"解析历史检查点文件 {f.name} 失败: {e}")
        config.logger.info(f"从磁盘预恢复了 {len(runs)} 条历史任务索引")
    except Exception as e:
        config.logger.warning(f"扫描磁盘检查点历史目录异常: {e}")


async def _emit_for(state: RunState):
    async def emit(ev: dict):
        ev = config.redact_data(dict(ev))
        if "ts" not in ev:
            ev["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        ev["run_id"] = state.id
        if ev.get('type') == 'report' and ev.get('path'):
            record={'type':ev.get('report_type','报告'),'path':ev['path'],'markdown':ev.get('markdown','')}
            state.reports=[r for r in state.reports if r['type']!=record['type']]+[record]
        # 先准备交互状态再广播，避免极速点击或刷新出现 409。
        if ev.get("type") == "confirm_required":
            state.status = "awaiting_confirm"
            state.ensure_future()
        if ev.get("type") == "gate_check" and ev.get("verdict") == "fail" and ev.get("interactive"):
            state.status = "awaiting_gate"
            state.pending_gate = dict(ev)
            state.ensure_gate_future()
        async with state.cond:
            state.timeline.consume(ev)
            if ev.get("type") in {"stage", "phase", "resumed", "confirm_required", "confirm_resolved", "sources_required", "sources_selected", "gate_check", "gate_resolved", "done", "error", "cancelled"}:
                ev["timeline"] = state.timeline.snapshot()
            if ev.get("type") == "plan": state.latest_plan = ev.get("plan")
            state.events.append(ev)
            state.cond.notify_all()
        state.updated_at = ev["ts"]
        if ev.get("type") in {"sources_selected", "sources_required", "confirm_required", "gate_check", "report_start", "report_interrupted", "operation_done", "operation_start", "execution_policy", "resumed"} or (ev.get("type") == "stage" and ev.get("status") == "done"):
            await _persist_events(state)

    return emit


async def _confirm_for(state: RunState):
    async def confirm(plan: dict) -> str:
        state.status = "awaiting_confirm"
        if state.confirm_future is None:
            state.ensure_future()
        if state.auto_confirm:
            if not state.confirm_future.done():
                state.confirm_future.set_result("")
        feedback = await state.confirm_future
        state.status = "running"
        return feedback

    return confirm


async def _gate_for(state: RunState):
    """构建关卡决策回调：核查未通过时挂起流水线，等待用户选择打回/继续。"""

    async def gate(payload: dict) -> dict:
        state.status = "awaiting_gate"
        state.pending_gate = payload
        if state.gate_future is None:
            state.ensure_gate_future()
        result = await state.gate_future
        state.pending_gate = None
        state.status = "running"
        return result

    return gate


async def _sources_for(state: RunState, emit):
    async def choose(payload: dict) -> list[str]:
        state.status = "awaiting_sources"
        state.sources_future = asyncio.get_running_loop().create_future()
        state.pending_sources = {**payload, "request_id": uuid.uuid4().hex}
        # Future 及状态先就绪，避免用户快速提交时丢失选择。
        await emit({"type": "sources_required", **state.pending_sources})
        try:
            selected = await state.sources_future
            state.search_sources = selected
            return selected
        finally:
            state.pending_sources = None
            if state.status == "awaiting_sources":
                state.status = "running"
    return choose


async def _run_task(state: RunState, resume: bool = False):
    emit = await _emit_for(state)
    confirm = await _confirm_for(state)
    choose_sources = await _sources_for(state, emit)
    # 交互模式关卡挂起等用户决策；auto_confirm（自动模式）交给流水线自动决策
    gate = None if state.auto_confirm else await _gate_for(state)
    state.status = "running"
    if resume:
        await emit({"type": "resumed", "run_id": state.id, "message": "已从上一次检查点断点恢复执行"})
    try:
        summary = await pipeline.run_pipeline(
            state.task,
            state.model_id,
            emit,
            confirm,
            run_id=state.id,
            role_models_override=state.role_models,
            resume=resume,
            gate=gate,
            select_sources=choose_sources,
            execution_mode=state.execution_mode,
        )
        state.summary = summary
        state.reports = [
            {"type": rt, "path": p, "markdown": _read_report(p)}
            for rt, p in _iter_reports(summary)
        ]
        state.status = "done"
        async with state.cond:
            state.cond.notify_all()
    except asyncio.CancelledError:
        state.status = "cancelled"
        config.logger.info(f"[{state.id}] 任务协程被取消")
        await emit({"type": "cancelled", "message": "任务已被主动取消"})
        async with state.cond:
            state.cond.notify_all()
    except Exception as e:  # noqa: BLE001
        state.status = "error"
        state.summary = {**state.summary, "error": f"{type(e).__name__}: {e}"}
        if not state.events or state.events[-1].get('type') != 'error':
            await emit({'type':'error','message':str(e),'can_resume':True})
        async with state.cond:
            state.cond.notify_all()
    finally:
        await _persist_events(state)


def _iter_reports(summary: dict):
    if summary.get("report_records"):
        for record in summary["report_records"]:
            yield record["type"], record["path"]
        return
    for p in summary.get("reports", []):
        rt = Path(p).stem.rsplit("_", 3)[0] if Path(p).stem else "报告"
        yield rt, p


def _read_report(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ---------- API ----------
@app.get("/api/search-sources")
async def list_search_sources():
    return {"sources": search_sources.source_options()}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "has_key": bool(config.DASHSCOPE_API_KEY),
        "default_model": config.resolve_model(),
    }


@app.get("/api/models")
async def list_models():
    default = config.resolve_model()
    return {
        "default": default,
        "role_defaults": models.ROLE_DEFAULTS,
        "role_reasons": models.ROLE_REASONS,
        "role_profiles": models.ROLE_PROFILES,
        "profile_reasons": models.PROFILE_REASONS,
        "sources": models.MODEL_SOURCES,
        "models": [
            {
                **m,
                "available": model_unavailable(m['id']) is None,
                "unavailable_reason": model_unavailable(m['id']),
                "is_default": m["id"] == default,
                "tier_rank": models.TIER_ORDER.get(m["tier"], 9),
            }
            for m in models.MODELS
        ],
    }


@app.get("/api/model-recommendation")
async def model_recommendation(task: str = "", execution_mode: Literal["auto", "quick", "standard", "deep"] = "auto"):
    policy = choose_policy(task, execution_mode)
    return {"mode": policy.mode, "label": policy.label, "role_models": models.role_defaults(policy.mode),
            "reasons": models.PROFILE_REASONS[policy.mode]}


class RunRequest(BaseModel):
    task: str
    model_id: Optional[str] = None
    auto_confirm: bool = False
    role_models: Optional[dict] = None
    execution_mode: Literal["auto", "quick", "standard", "deep"] = "auto"


def model_unavailable(model_id):
    try:
        data=json.loads((config.OUTPUT_DIR/'acceptance_models.json').read_text(encoding='utf-8'))
        if not isinstance(data,dict) or time.time()-data['tested_at']>3600:return None
        if data.get('account_fingerprint')!=hashlib.sha256(config.DASHSCOPE_API_KEY.encode()).hexdigest():return None
        row=next((m for m in data['models'] if m['id']==model_id),{})
        if not row.get('ok',True) and '403' in row.get('error','') and 'quota' in row.get('error','').lower():
            return '当前账户免费额度耗尽（403），请在账户条件变更后重新测试；未自动启用付费'
    except (OSError,ValueError,TypeError,KeyError):pass
    return None


def validated_roles(overrides, mode="standard"):
    roles = models.role_defaults(mode)
    for role, value in (overrides or {}).items():
        if role not in roles or not isinstance(value, str) or not models.get_model(value):
            raise HTTPException(status_code=400, detail=f"无效的岗位模型配置：{role}")
        if model_unavailable(value):
            raise HTTPException(status_code=400,detail=f'{value}：{model_unavailable(value)}')
        roles[role] = value
    return roles


@app.post("/api/runs")
async def create_run(req: RunRequest):
    task = (req.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="任务不能为空")
    model_id = config.resolve_model(req.model_id)
    if not models.get_model(model_id):
        raise HTTPException(status_code=400, detail=f"未知模型：{model_id}")
    state = RunState(task, model_id, auto_confirm=req.auto_confirm,
                     role_models=validated_roles(req.role_models, choose_policy(task, req.execution_mode).mode), execution_mode=req.execution_mode)
    state.ensure_future()
    _add_run(state)
    state.task_obj = asyncio.create_task(_run_task(state))
    config.logger.info(f"[{state.id}] 创建新任务: {task[:40]}")
    return {"run_id": state.id, "model_id": model_id}


class ResumeRequest(BaseModel):
    use_recommended: bool = False
    role_models: Optional[dict] = None


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest | None = None):
    """从中断/失败的检查点恢复执行"""
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    if state.status not in {"error", "cancelled"} or (state.task_obj and not state.task_obj.done()):
        raise HTTPException(status_code=409, detail="仅可恢复已停止的失败或取消任务；等待选择渠道的任务请先完成选择")
    checkpoint = pipeline.load_checkpoint(run_id)
    if not checkpoint:
        raise HTTPException(status_code=409, detail="检查点不存在或损坏，请新建任务；不会自动从头重跑")
    try:
        info=resume_info(checkpoint,run_id)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(status_code=409,detail=f'检查点不完整，已保留原文件：{exc}') from exc
    if checkpoint.get('status') == 'done':
        raise HTTPException(status_code=409,detail='该任务已完成，请查看最终报告，无需恢复')
    if req and (req.use_recommended or req.role_models):
        mode = choose_policy(state.task, checkpoint.get("execution_mode", state.execution_mode)).mode
        state.role_models = validated_roles(None if req.use_recommended else req.role_models, mode)
    state.pending_gate = None
    state.pending_sources = None
    for future in (state.confirm_future, state.gate_future, state.sources_future):
        if future and not future.done():
            future.cancel()
    state.confirm_future = state.gate_future = state.sources_future = None
    state.summary.pop("error", None)

    config.logger.info(f"[{run_id}] 启动断点续传恢复流水线")
    state.status = "running"
    state.ensure_future()
    state.task_obj = asyncio.create_task(_run_task(state, resume=True))
    return {"ok": True, "run_id": run_id, "resume_info":info}


@app.get("/api/runs")
async def list_runs():
    return {"runs": [s.to_summary() for s in sorted(runs.values(), key=lambda s: s.created_at, reverse=True)]}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    return state.to_detail()


class ConfirmRequest(BaseModel):
    feedback: str = ""


class SourcesRequest(BaseModel):
    request_id: str
    sources: list[str]


@app.post("/api/runs/{run_id}/sources")
async def select_run_sources(run_id: str, req: SourcesRequest):
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    if state.status != "awaiting_sources" or not state.pending_sources or not state.sources_future:
        raise HTTPException(status_code=409, detail="当前没有等待搜索渠道选择")
    if req.request_id != state.pending_sources["request_id"]:
        raise HTTPException(status_code=409, detail="渠道选择已更新，请刷新当前任务")
    try:
        selected = search_sources.validate_sources(req.sources)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if state.sources_future.done():
        return {"ok": True, "already": True}
    state.sources_future.set_result(selected)
    return {"ok": True, "sources": selected}


@app.post("/api/runs/{run_id}/confirm")
async def confirm_run(run_id: str, req: ConfirmRequest):
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    if state.status != "awaiting_confirm" or not state.confirm_future:
        raise HTTPException(status_code=409, detail="当前没有待确认计划")
    if state.confirm_future.done():
        return {"ok": True, "already": True}
    state.confirm_future.set_result((req.feedback or "").strip())
    config.logger.info(f"[{run_id}] 用户完成计划确认，反馈: {req.feedback}")
    return {"ok": True}


class GateRequest(BaseModel):
    decision: Literal['redo','continue']
    feedback: Optional[str] = ""


@app.post("/api/runs/{run_id}/gate")
async def gate_run(run_id: str, req: GateRequest):
    """用户对核查未通过的关卡做出决策：打回重做（redo）或继续执行（continue）。"""
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    if state.status != "awaiting_gate" or not state.pending_gate:
        raise HTTPException(status_code=409, detail="当前没有等待决策的核查关卡")
    if state.gate_future is None:
        state.ensure_gate_future()
    if state.gate_future.done():
        return {"ok": True, "already": True}
    decision = "redo" if req.decision == "redo" else "continue"
    state.gate_future.set_result({"decision": decision, "feedback": (req.feedback or "").strip()})
    config.logger.info(f"[{run_id}] 用户关卡决策: {decision}（阶段 {state.pending_gate.get('stage')}），反馈: {req.feedback}")
    return {"ok": True}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    if state.status in FINAL:
        raise HTTPException(status_code=409,detail='任务已停止，不能再次取消')
    config.logger.info(f"[{run_id}] 收到终止任务请求")
    if state.confirm_future and not state.confirm_future.done():
        state.confirm_future.set_result("")
    if state.gate_future and not state.gate_future.done():
        state.gate_future.set_result({"decision": "continue", "feedback": ""})
    if state.sources_future and not state.sources_future.done():
        state.sources_future.cancel()
    state.pending_sources = None
    if state.task_obj and not state.task_obj.done():
        state.task_obj.cancel()
    state.status = "cancelled"
    emit = await _emit_for(state)
    await emit({"type": "cancelled", "message": "任务已被主动取消"})
    async with state.cond:
        state.cond.notify_all()
    await _persist_events(state)
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = -1):
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")

    async def gen():
        last = max(-1, after)
        last_hdr = request.headers.get("last-event-id")
        if last_hdr:
            try:
                last = int(last_hdr)
            except ValueError:
                last = -1
        idx = min(max(0,last + 1),len(state.events))
        try:
            while True:
                async with state.cond:
                    try:
                        await asyncio.wait_for(state.cond.wait_for(
                            lambda: idx < len(state.events) or state.status in FINAL
                        ), timeout=15)
                    except asyncio.TimeoutError:
                        pass
                    new = state.events[idx:]
                    idx = len(state.events)
                    finished = state.status in FINAL and not new
                for i, ev in enumerate(new):
                    eid = idx - len(new) + i
                    yield f"id: {eid}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if finished:
                    yield f"data: {json.dumps({'type': 'stream_end'}, ensure_ascii=False)}\n\n"
                    break
                if not new:
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            yield f"data: {json.dumps({'type': 'stream_end'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/runs/{run_id}/report.md")
async def download_report_md(run_id: str):
    """合并该任务全部报告为单一 Markdown 文件，以附件形式触发浏览器下载。"""
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")
    if not state.reports:
        raise HTTPException(status_code=404, detail="报告尚未生成")

    parts = []
    for i, r in enumerate(state.reports, 1):
        md = (r.get("markdown") or "").strip()
        if not md:
            md = _read_report(r.get("path", ""))
        if md:
            parts.append(f"# {r.get('type') or f'报告{i}'}\n\n{md}")
    if not parts:
        raise HTTPException(status_code=404, detail="报告文件读取失败")

    content = "\n\n---\n\n".join(parts) + "\n"
    # 中文文件名需 RFC 5987 编码；filename= 给出 ASCII 兜底，避免响应头 latin-1 编码崩溃
    fname = f"OSINT报告_{run_id}.md"
    disposition = (
        f"attachment; filename=\"report_{run_id}.md\"; filename*=UTF-8''{quote(fname)}"
    )
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@app.get("/api/reports/{run_id}/{filename}")
async def get_report_file(run_id: str, filename: str):
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行不存在")

    # 安全检查：只允许读取该任务自产、且位于报告目录内的文件，杜绝路径遍历与越权读取
    if (
        not filename
        or ".." in filename
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or run_id not in filename
    ):
        raise HTTPException(status_code=404, detail="文件不存在")

    p = (config.REPORT_DIR / filename).resolve()
    if config.REPORT_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if p not in {Path(r['path']).resolve() for r in state.reports if r.get('path')}:
        raise HTTPException(status_code=404,detail='文件不属于该任务')

    content = await asyncio.to_thread(p.read_text, encoding="utf-8")
    return JSONResponse({"filename": p.name, "content": content})


def main():
    config.check_key()
    _restore_history_from_disk()
    config.logger.info(f"OSINT 多智能体服务启动：http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
