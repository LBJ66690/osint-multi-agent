# server.py -- 开源情报多智能体平台的 Web 服务
# FastAPI + SSE：驱动 pipeline.py，支持断点续传、内存LRU管理、历史恢复与全链路日志
from __future__ import annotations

import asyncio
import json
import uuid
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
import pymysql
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from urllib.parse import quote

import config
import models
import pipeline
import search_sources
import report_chat
import sag_evidence
import sag_report_import
import hashlib
import travel_api
import travel_plan
import travel_store
import web_auth
from bailian_models import QUOTA_EXPIRY
from mysql_store import MySQLStore, mysql_settings
from resource_usage import snapshot as resource_snapshot
from runtime_state import RunTimeline
from execution_policy import choose_policy
from checkpoint_resume import resume_info

ROOT = config.ROOT
STATIC = Path(os.getenv("TRAVEL_STATIC_DIR", ROOT / "static"))

@asynccontextmanager
async def lifespan(app):
    from asyncio_compat import install_cleanup_handler
    restore = install_cleanup_handler(asyncio.get_running_loop())
    store = None
    try:
        if web_auth.online_enabled():
            app.state.online_settings = web_auth.OnlineSettings()
            store = MySQLStore(mysql_settings())
            try:
                await asyncio.to_thread(store.check_schema)
                await asyncio.to_thread(store.recover_interrupted_runs)
                await asyncio.to_thread(travel_store.reconcile,store)
            except pymysql.MySQLError:
                raise RuntimeError("无法连接 MySQL 或数据库表未初始化。请检查数据库配置和建表脚本。") from None
            app.state.online_store = store
            app.state.create_lock = asyncio.Lock()
        yield
    finally:
        if store:
            tasks = [state.task_obj for state in runs.values()
                     if state.store is store and state.task_obj and not state.task_obj.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for run_id in [key for key, state in runs.items() if state.store is store]:
                runs.pop(run_id)
            app.state.online_store = None
        restore()


app = FastAPI(title="开源情报多智能体平台", version="2.5", lifespan=lifespan)
app.include_router(web_auth.router)
app.include_router(travel_api.router)

PUBLIC_API = {"/api/health", "/api/auth/options", "/api/auth/register", "/api/auth/login", "/api/auth/logout"}


@app.exception_handler(RequestValidationError)
async def hide_auth_input(request, exc):
    if request.url.path.startswith("/api/auth/"):
        return JSONResponse({"detail": "账户表单格式有误，请检查后重试。"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(pymysql.MySQLError)
async def database_error(_request, exc):
    config.logger.error("MySQL 操作失败，错误类型 %s", type(exc).__name__)
    return JSONResponse({"detail": "数据库暂时不可用，请稍后再试。"}, status_code=503)


@app.middleware("http")
async def protect_online_requests(request: Request, call_next):
    if not web_auth.online_enabled():
        return await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        response = await call_next(request)
    else:
        store = web_auth.store_for(request)
        if not store:
            return JSONResponse({"detail": "服务正在连接数据库。"}, status_code=503)
        settings = request.app.state.online_settings
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != settings.origin:
            return JSONResponse({"detail": "请求来源与站点地址不匹配。"}, status_code=403)
        request.state.user = None
        try:
            if path not in PUBLIC_API or path == "/api/health":
                digest = web_auth.session_digest(request)
                if digest:
                    request.state.user = await asyncio.to_thread(store.session_user, digest)
            if path.startswith("/api/") and path not in PUBLIC_API and not request.state.user:
                return JSONResponse({"detail": "请先登录。"}, status_code=401, headers={"Cache-Control": "no-store"})
            response = await call_next(request)
        except pymysql.MySQLError as exc:
            response = await database_error(request, exc)
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response

FINAL = {"done", "error", "cancelled"}
MAX_RUNS = 60  # 内存中最大保留任务数，避免长时间运行 OOM


class RunState:
    def __init__(self, task: str, model_id: str, auto_confirm: bool = False,
                 role_models: Optional[dict] = None, execution_mode: str = "auto"):
        self.id = uuid.uuid4().hex[:12]
        self.owner_id: Optional[int] = None
        self.store: Optional[MySQLStore] = None
        self.persisted_events = 0
        self.travel = None
        self.task = task
        self.model_id = model_id
        self.role_models = role_models or models.role_defaults(choose_policy(task, execution_mode).mode)
        self.role_overrides = role_models
        self.execution_mode = execution_mode
        self.search_strategy = 'legacy'
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
            "search_strategy": self.search_strategy,
        }

    def to_detail(self, checkpoint=None) -> dict:
        recovery=None
        if self.status in {'error','cancelled'}:
            try:
                recovery=resume_info(checkpoint if self.store else pipeline.load_checkpoint(self.id),self.id)
            except (ValueError,TypeError,KeyError):
                recovery={'unavailable':True,'message':'检查点缺失或不完整，无法安全恢复'}
        return {
            **self.to_summary(),
            **({k:self.summary.get(k) for k in ("trip_plan","evidence","domain","data_mode")} if self.travel else {}),
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
    if state.store:
        async with state.persist_lock:
            events = state.events[state.persisted_events:]
            await asyncio.to_thread(
                state.store.save_run_progress, state.id, state.to_summary(), state.persisted_events,
                events, config.redact_data(state.summary) if state.status in FINAL else None,
            )
            state.persisted_events += len(events)
        return
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
                if data.get("model_provider", "bailian") != config.model_provider():
                    continue
                rid = data.get("run_id")
                if rid and rid not in runs:
                    st = RunState(
                        task=data.get("task", ""),
                        model_id=data.get("model_id", ""),
                        role_models=data.get("role_models"),
                        execution_mode=data.get("execution_mode", "auto"),
                    )
                    st.id = rid
                    st.travel = data.get("travel")
                    st.search_sources = data.get("search_sources", [])
                    st.search_strategy = data.get('search_strategy','legacy')
                    is_done = data.get("status") == "done" if data.get("schema_version") == 2 else "verifying" in data.get("completed_stages", [])
                    st.status = "done" if is_done else "error"
                    st.reports = [
                        {"type": rt, "path": p, "markdown": _read_report(p)}
                        for rt, p in _iter_reports(data)
                    ]
                    st.summary = {
                        "model_provider": data.get("model_provider", "bailian"),
                        "routing": data.get("routing"),
                        "reports": data.get("reports", []),
                        "plan": data.get("plan"),
                        "evidence": data.get("evidence", []),
                        "knowledge": data.get("knowledge"),
                        "findings": data.get("analysis", {}).get("findings", []),
                        "verifications": data.get("verifications", {}),
                    }
                    if st.travel:
                        st.summary.update(data)
                    st.events = _load_events(rid)
                    st.summary.update({key:data.get(key,[]) if key in {'decision_history','conflicts'} else data.get(key,{})
                                       for key in ['decision_history','conflicts','evidence_metrics','search_metrics']})
                    st.summary['resource_usage'] = resource_snapshot(data['resource_usage']) if data.get('resource_usage') else None
                    st.summary['unresolved_gaps'] = data.get('unresolved_gaps',[])
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
        if ev.get('type') == 'resource_usage':
            state.summary['resource_usage'] = ev.get('stats')
        if ev.get('type') == 'execution_policy':
            state.execution_mode = ev['mode']
            state.role_models = ev['role_models']
        if ev.get('type') == 'evidence_quality':
            state.summary['evidence_metrics'] = ev.get('metrics',{})
            state.summary['conflicts'] = ev.get('conflicts',state.summary.get('conflicts',[]))
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
        if ev.get("type") in {"sources_selected", "sources_required", "confirm_required", "gate_check", "report_start", "report_interrupted", "operation_done", "operation_start", "operation_error", "model_response", "model_error", "resource_usage", "execution_policy", "resumed"} or (ev.get("type") == "stage" and ev.get("status") == "done"):
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
    try:
        if resume:
            await emit({"type": "resumed", "run_id": state.id, "message": "已从上一次检查点断点恢复执行"})
        if state.travel:
            summary = await travel_plan.execute(state, emit)
        else:
            summary = await pipeline.run_pipeline(
                state.task,
                state.model_id,
                emit,
                confirm,
                run_id=state.id,
                role_models_override=state.role_overrides,
                resume=resume,
                gate=gate,
                select_sources=choose_sources,
                execution_mode=state.execution_mode,
                search_strategy=state.search_strategy,
                **({"store": state.store} if state.store else {}),
            )
        state.summary = summary
        state.reports = await _run_reports(state, summary)
        if state.travel:
            if not state.reports or not state.reports[0]["markdown"]:
                raise RuntimeError("旅行报告未能回读")
            if state.store:
                await asyncio.to_thread(travel_store.transition,state.store,state.owner_id,state.id,"used")
            await emit({"type":"done","message":"旅行报告已保存"})
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
        message = "数据库暂时不可用，任务已暂停。" if isinstance(e, pymysql.MySQLError) else str(e)
        state.summary = {**state.summary, "error": config.redact_secret(message)}
        if not state.events or state.events[-1].get('type') != 'error':
            await emit({'type':'error','message':message,'can_resume':True})
        async with state.cond:
            state.cond.notify_all()
    finally:
        try:
            if state.store and state.travel and state.status in ("cancelled","error"):
                await asyncio.to_thread(travel_store.transition,state.store,state.owner_id,state.id,"released")
            await _persist_events(state)
        except pymysql.MySQLError:
            config.logger.error("[%s] 暂未保存任务最终进度", state.id)


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


async def _run_reports(state, summary):
    saved = await asyncio.to_thread(state.store.read_reports, state.id) if state.store else None
    return [
        {"type": rt, "path": path, "markdown": _read_report(path) if saved is None else saved.get(Path(path).name, "")}
        for rt, path in _iter_reports(summary)
    ]


async def _get_run_state(run_id, request=None):
    store = web_auth.store_for(request)
    if not store:
        state = runs.get(run_id)
        if state is None:
            raise HTTPException(404, "运行不存在")
        return state
    owner_id = request.state.user["id"]
    state = runs.get(run_id)
    if state is not None:
        if state.store is store and state.owner_id == owner_id:
            return state
        raise HTTPException(404, "运行不存在")
    row = await asyncio.to_thread(store.owned_run, owner_id, run_id)
    if row is None:
        raise HTTPException(404, "运行不存在")
    state = runs.get(run_id)
    if state is not None and state.store is store and state.owner_id == owner_id:
        return state
    checkpoint, events = await asyncio.gather(
        asyncio.to_thread(store.load_checkpoint, run_id),
        asyncio.to_thread(store.load_events, run_id),
    )
    checkpoint = checkpoint or {}
    state = RunState(row["task"], row["model_id"], role_models=checkpoint.get("role_models"),
                     execution_mode=row["execution_mode"])
    state.id, state.owner_id, state.store = run_id, owner_id, store
    state.travel = await asyncio.to_thread(travel_store.load_request,store,owner_id,run_id)
    state.status = row["status"]
    state.search_strategy = row["search_strategy"]
    state.created_at, state.updated_at = row["created_at"], row["updated_at"]
    state.search_sources = checkpoint.get("search_sources", [])
    state.summary = row["summary"] or {
        "plan": checkpoint.get("plan"),
        "evidence": checkpoint.get("evidence", []),
        "findings": checkpoint.get("adaptive_findings") or (checkpoint.get("analysis") or {}).get("findings", []),
        "verifications": checkpoint.get("verifications", {}),
        "resource_usage": checkpoint.get("resource_summary"),
        "unresolved_gaps": checkpoint.get("unresolved_gaps", []),
        "conflicts": checkpoint.get("conflicts", []),
        "evidence_metrics": checkpoint.get("evidence_metrics", {}),
    }
    if state.travel and checkpoint:
        state.summary.update(checkpoint)
    state.reports = await _run_reports(state, checkpoint if checkpoint.get("reports") else state.summary)
    state.events, state.persisted_events = events, len(events)
    for event in events:
        if event.get("ts"):
            state.timeline.consume(event)
        if event.get("type") == "plan":
            state.latest_plan = event.get("plan")
    if state.status in FINAL and state.timeline.ended is None:
        state.timeline.consume({"type": state.status, "ts": state.updated_at})
    return state


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if web_auth.store_for(request) and not request.state.user:
        return RedirectResponse("/login", status_code=303)
    page = STATIC / "index.html"
    if request.query_params.get("workspace")=="research" and (STATIC/"workspace.html").is_file():
        page=STATIC/"workspace.html"
    return page.read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
@app.get("/register", response_class=HTMLResponse)
async def login_page(request: Request):
    if not web_auth.store_for(request) or request.state.user:
        return RedirectResponse("/", status_code=303)
    return (STATIC / "auth.html").read_text(encoding="utf-8")


@app.get("/static/travel/config.js",include_in_schema=False)
def travel_browser_config():
    public={"amapKey":"","amapServiceHost":"","demoOrderSku":"travel-one"}
    return Response("export const travelConfig = "+json.dumps(public)+";",media_type="text/javascript")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ---------- API ----------
@app.get("/api/search-sources")
async def list_search_sources(request: Request = None):
    return {"sources": search_sources.source_options(free_only=bool(web_auth.store_for(request)))}


@app.get("/api/health")
async def health(request: Request = None):
    store = web_auth.store_for(request)
    if store:
        if not await asyncio.to_thread(store.healthy):
            return JSONResponse({"ok": False}, status_code=503)
        if not getattr(request.state, "user", None):
            return {"ok": True, "auth_enabled": True}
    return {
        "ok": True,
        "has_key": bool(config.chat_api_key()),
        "model_ready": bool(config.chat_api_key() and config.bailian_free_only_confirmed() and models.default_model_id()),
        "auth_enabled": bool(store),
        "default_model": models.default_model_id(),
        "experiment_mode": config.EXPERIMENT_MODE,
        "model_provider": config.model_provider(),
    }


@app.get("/api/models")
async def list_models(request: Request = None):
    default = models.default_model_id()
    return {
        "default": default,
        "experiment_mode": config.EXPERIMENT_MODE,
        "sag_enabled": not web_auth.store_for(request) and sag_evidence.settings(config) is not None,
        "role_defaults": models.role_defaults("quick"),
        "role_reasons": models.role_reasons("quick"),
        "role_profiles": {mode: models.role_defaults(mode) for mode in models.ROLE_PROFILES},
        "profile_reasons": {mode: models.role_reasons(mode) for mode in models.ROLE_PROFILES},
        "sources": ([{"title": "AMD Radeon Cloud", "url": "https://developer.amd.com.cn/"}]
                    if config.EXPERIMENT_MODE else models.MODEL_SOURCES),
        "models": [
            {
                **m,
                "available": model_unavailable(m['id']) is None,
                "unavailable_reason": model_unavailable(m['id']),
                "quota_expiry": QUOTA_EXPIRY[m["id"]].isoformat() if m["id"] in QUOTA_EXPIRY else None,
                "is_default": m["id"] == default,
                "tier_rank": models.TIER_ORDER.get(m["tier"], 9),
            }
            for m in models.catalog()
        ],
    }


@app.get("/api/model-recommendation")
async def model_recommendation(task: str = "", execution_mode: Literal["auto", "quick", "standard", "deep"] = "auto"):
    policy = choose_policy(task, execution_mode)
    return {"mode": policy.mode, "label": policy.label, "role_models": models.role_defaults(policy.mode),
            "reasons": models.role_reasons(policy.mode),
            "semantic_on_start": execution_mode == "auto"}


class RunRequest(BaseModel):
    domain: Optional[Literal["guizhou_travel"]] = None
    trip: Optional[travel_api.Trip] = None
    attachment_ids: list[str] = Field(default_factory=list,max_length=8)
    task: str = Field(max_length=6000)
    model_id: Optional[str] = None
    auto_confirm: bool = False
    role_models: Optional[dict] = None
    execution_mode: Literal["auto", "quick", "standard", "deep"] = "auto"
    search_strategy: Literal['legacy','adaptive'] = 'legacy'


def model_unavailable(model_id):
    if config.EXPERIMENT_MODE:
        return "Radeon 实验模型已暂停对外调用。"
    return config.bailian_unavailable(model_id)


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
async def create_run(req: RunRequest, request: Request = None):
    task = (req.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="任务不能为空")
    try:
        model_id = config.resolve_model(req.model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = RunState(task, model_id, auto_confirm=req.auto_confirm,
                     role_models=validated_roles(req.role_models, choose_policy(task, req.execution_mode).mode), execution_mode=req.execution_mode)
    state.role_overrides = req.role_models
    state.ensure_future()
    state.search_strategy = req.search_strategy
    store = web_auth.store_for(request)
    if req.domain:
        if not req.trip or req.trip.missing():
            raise HTTPException(422,"请先确认完整旅行草稿")
        key=request.headers.get("Idempotency-Key","") if request else ""
        if not 1<=len(key.encode())<=128:
            raise HTTPException(422,"需要有效的 Idempotency-Key")
        state.travel={"trip":req.trip.model_dump(mode="json",exclude_none=True),"attachment_ids":req.attachment_ids}
        fingerprint=hashlib.sha256(json.dumps([task,state.travel],sort_keys=True).encode()).hexdigest()
        state.summary={"domain":"guizhou_travel","request_key":key,"fingerprint":fingerprint}
        if store:
            state.id=uuid.uuid4().hex
            state.store,state.owner_id=travel_api.owner(request)
            async with request.app.state.create_lock:
                saturated=sum(s.owner_id==state.owner_id and s.status not in FINAL for s in runs.values())>=2 or (len(runs)>=MAX_RUNS and all(s.status not in FINAL for s in runs.values()))
                existing=await asyncio.to_thread(travel_store.reserve,store,state.owner_id,state.to_summary(),key,fingerprint,state.travel,saturated,request.app.state.online_settings.max_daily_runs)
                if existing!=state.id:
                    return {"run_id":existing,"model_id":model_id}
        else:
            if req.attachment_ids:
                raise HTTPException(401,"附件需要登录")
            for old in runs.values():
                if old.store is None and old.travel and old.summary.get("request_key")==key:
                    if old.summary.get("fingerprint")!=fingerprint:
                        raise HTTPException(409,{"code":"idempotency_conflict","message":"请求内容已变更"})
                    return {"run_id":old.id,"model_id":old.model_id}
        _add_run(state)
    elif store:
        require_model_service()
        state.id = uuid.uuid4().hex
        state.store = store
        state.owner_id = request.state.user["id"]
        async with request.app.state.create_lock:
            check_user_capacity(state.owner_id)
            await web_auth.use_limit(store, "run", state.owner_id, 86400,
                                     request.app.state.online_settings.max_daily_runs, "今日研究任务次数已达上限。")
            _add_run(state)
            try:
                await asyncio.to_thread(store.create_run, state.owner_id, state.to_summary())
            except BaseException:
                runs.pop(state.id, None)
                raise
    else:
        _add_run(state)
    state.task_obj = asyncio.create_task(_run_task(state))
    config.logger.info("[%s] 创建新任务", state.id)
    return {"run_id": state.id, "model_id": model_id}


def require_model_service():
    if not config.chat_api_key() or not config.bailian_free_only_confirmed():
        raise HTTPException(503, "模型服务尚未启用。请配置百炼密钥并确认免费额度用完即停已开启。")
    if not models.default_model_id():
        raise HTTPException(503, "当前没有可用的免费额度模型。")


def check_user_capacity(owner_id):
    if sum(state.owner_id == owner_id and state.status not in FINAL for state in runs.values()) >= 2:
        raise HTTPException(429, "已有两项研究任务在执行或等待确认。")


@app.get('/api/comparison')
async def get_comparison():
    output_dir = config.ROOT / 'outputs' if config.EXPERIMENT_MODE else config.OUTPUT_DIR
    path=output_dir/'comparison/latest.json'
    if not path.is_file():
        raise HTTPException(404,'请先运行 python scripts/compare_search_strategies.py；不调用真实模型或搜索。')
    try:return json.loads(await asyncio.to_thread(path.read_text,encoding='utf-8'))
    except (OSError,ValueError):raise HTTPException(409,'对比报告不完整，请重新生成。')


class ResumeRequest(BaseModel):
    use_recommended: bool = False
    role_models: Optional[dict] = None


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest | None = None, request: Request = None):
    """从中断/失败的检查点恢复执行"""
    state = await _get_run_state(run_id, request)
    if state.status not in {"error", "cancelled"} or (state.task_obj and not state.task_obj.done()):
        raise HTTPException(status_code=409, detail="仅可恢复已停止的失败或取消任务；等待选择渠道的任务请先完成选择")
    if state.travel:
        if state.store:
            async with request.app.state.create_lock:
                current=runs.get(run_id,state)
                if current.task_obj and not current.task_obj.done():
                    raise HTTPException(409,"任务正在执行")
                await asyncio.to_thread(travel_store.transition,state.store,state.owner_id,run_id,"reserved")
                _add_run(state)
                state.status="running"
                state.task_obj=asyncio.create_task(_run_task(state,resume=True))
        else:
            state.status="running"
            state.task_obj=asyncio.create_task(_run_task(state,resume=True))
        return {"ok":True,"run_id":run_id}
    if state.store:
        require_model_service()
        checkpoint = await asyncio.to_thread(state.store.load_checkpoint, run_id)
        current = runs.get(run_id)
        if current and (current.status not in {"error", "cancelled"} or (current.task_obj and not current.task_obj.done())):
            raise HTTPException(409, "该任务正在恢复或已完成。")
        if current:
            state = current
        check_user_capacity(state.owner_id)
    else:
        checkpoint = pipeline.load_checkpoint(run_id)
    if not checkpoint:
        raise HTTPException(status_code=409, detail="检查点不存在或损坏，请新建任务；不会自动从头重跑")
    if checkpoint.get("model_provider", "bailian") != config.model_provider():
        raise HTTPException(status_code=409, detail="请在创建该任务的模型服务模式下恢复检查点。")
    try:
        info=resume_info(checkpoint,run_id)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(status_code=409,detail=f'检查点不完整，已保留原文件：{exc}') from exc
    if checkpoint.get('status') == 'done':
        raise HTTPException(status_code=409,detail='该任务已完成，请查看最终报告，无需恢复')
    if req and (req.use_recommended or req.role_models):
        mode = choose_policy(state.task, checkpoint.get("execution_mode", state.execution_mode)).mode
        state.role_models = validated_roles(None if req.use_recommended else req.role_models, mode)
        state.role_overrides = state.role_models
    else:
        state.role_overrides = None
    if state.store and run_id not in runs:
        _add_run(state)
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
async def list_runs(request: Request = None):
    store = web_auth.store_for(request)
    if store:
        owner_id = request.state.user["id"]
        history = await asyncio.to_thread(store.list_runs, owner_id)
        live = {state.id: state.to_summary() for state in runs.values()
                if state.store is store and state.owner_id == owner_id}
        return {"runs": [live.get(row["id"], row) for row in history]}
    return {"runs": [s.to_summary() for s in sorted(runs.values(), key=lambda s: s.created_at, reverse=True)]}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request = None):
    state = await _get_run_state(run_id, request)
    checkpoint = None
    if state.store and state.status in {"error", "cancelled"}:
        checkpoint = await asyncio.to_thread(state.store.load_checkpoint, state.id)
    return state.to_detail(checkpoint)


@app.get("/api/runs/{run_id}/sag-evidence/{evidence_id}")
async def get_sag_evidence(run_id: str, evidence_id: str, request: Request = None):
    if web_auth.store_for(request):
        raise HTTPException(404, "线上模式不访问本机资料。")
    if run_id in runs:
        checkpoint = await asyncio.to_thread(pipeline.load_checkpoint, run_id)
        suffix = f"/api/runs/{run_id}/sag-evidence/{evidence_id}"
        for item in (checkpoint or {}).get("evidence", []):
            if item.get("provider") == "sag" and item.get("source", "").endswith(suffix):
                body = f"{item['title']}\n\n{item['content']}"
                return Response(body, media_type="text/plain", headers={"Cache-Control": "no-store"})
    raise HTTPException(404, "本地证据不存在")


class SagImportRequest(BaseModel):
    report_type: str = Field(min_length=1, max_length=200)
    report_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_ids: list[int] = Field(min_length=1, max_length=100)


async def _sag_report(run_id, report_type):
    if run_id not in runs:
        raise HTTPException(404, "报告不存在")
    settings = sag_evidence.settings(config)
    if not settings:
        raise HTTPException(409, "请设置 OSINT_SAG_SCOPE 指向已索引的资料目录，再重启项目。")
    index, scope = settings
    try:
        text = await asyncio.to_thread(report_chat.report_text, runs[run_id], report_type)
        target = await asyncio.to_thread(sag_report_import.destination, index, scope)
    except report_chat.ChatError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError:
        raise HTTPException(503, "暂时无法读取报告或访问 SAG 目录。") from None
    version, _ = report_chat.identity(run_id, report_type, text)
    return index, target, version, sag_report_import.sections(text)


@app.get("/api/runs/{run_id}/sag-import")
async def preview_sag_import(run_id: str, report_type: str, request: Request = None):
    if web_auth.store_for(request):
        raise HTTPException(404, "线上模式不访问本机资料。")
    _, _, version, sections = await _sag_report(run_id, report_type)
    return {"report_version": version, "sections": sections}


@app.post("/api/runs/{run_id}/sag-import")
async def import_to_sag(run_id: str, req: SagImportRequest, request: Request = None):
    if web_auth.store_for(request):
        raise HTTPException(404, "线上模式不访问本机资料。")
    index, target, version, sections = await _sag_report(run_id, req.report_type)
    if version != req.report_version:
        raise HTTPException(409, "报告已更新，请重新打开后选择片段。")
    ids = set(req.section_ids)
    selected = [section for section in sections if section["id"] in ids]
    if len(selected) != len(ids):
        raise HTTPException(422, "包含无效的报告片段。")
    try:
        path, fingerprint = await asyncio.to_thread(
            sag_report_import.save, target, run_id, req.report_type, version, selected)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "无法保存片段，请检查 SAG 资料目录的写入权限。") from None
    for attempt in range(9):
        if await asyncio.to_thread(sag_report_import.is_indexed, index, path, fingerprint):
            return {"status": "indexed", "file": path.name, "count": len(selected)}
        if attempt < 8:
            await asyncio.sleep(.5)
    return {"status": "pending", "file": path.name, "count": len(selected)}


class ConfirmRequest(BaseModel):
    feedback: str = ""


class SourcesRequest(BaseModel):
    request_id: str
    sources: list[str]


@app.post("/api/runs/{run_id}/sources")
async def select_run_sources(run_id: str, req: SourcesRequest, request: Request = None):
    state = await _get_run_state(run_id, request)
    if state.status != "awaiting_sources" or not state.pending_sources or not state.sources_future:
        raise HTTPException(status_code=409, detail="当前没有等待搜索渠道选择")
    if req.request_id != state.pending_sources["request_id"]:
        raise HTTPException(status_code=409, detail="渠道选择已更新，请刷新当前任务")
    try:
        selected = search_sources.validate_sources(req.sources)
        if state.store and "bailian" in selected:
            raise ValueError("线上模式仅开放公开检索渠道。")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if state.sources_future.done():
        return {"ok": True, "already": True}
    state.sources_future.set_result(selected)
    return {"ok": True, "sources": selected}


@app.post("/api/runs/{run_id}/confirm")
async def confirm_run(run_id: str, req: ConfirmRequest, request: Request = None):
    state = await _get_run_state(run_id, request)
    if state.status != "awaiting_confirm" or not state.confirm_future:
        raise HTTPException(status_code=409, detail="当前没有待确认计划")
    if state.confirm_future.done():
        return {"ok": True, "already": True}
    state.confirm_future.set_result((req.feedback or "").strip())
    config.logger.info("[%s] 用户完成计划确认", run_id)
    return {"ok": True}


class GateRequest(BaseModel):
    decision: Literal['redo','continue']
    feedback: Optional[str] = ""


@app.post("/api/runs/{run_id}/gate")
async def gate_run(run_id: str, req: GateRequest, request: Request = None):
    """用户对核查未通过的关卡做出决策：打回重做（redo）或继续执行（continue）。"""
    state = await _get_run_state(run_id, request)
    if state.status != "awaiting_gate" or not state.pending_gate:
        raise HTTPException(status_code=409, detail="当前没有等待决策的核查关卡")
    if state.gate_future is None:
        state.ensure_gate_future()
    if state.gate_future.done():
        return {"ok": True, "already": True}
    decision = "redo" if req.decision == "redo" else "continue"
    state.gate_future.set_result({"decision": decision, "feedback": (req.feedback or "").strip()})
    config.logger.info("[%s] 用户关卡决策: %s", run_id, decision)
    return {"ok": True}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request = None):
    state = await _get_run_state(run_id, request)
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
        if state.travel:
            await asyncio.gather(state.task_obj,return_exceptions=True)
    if state.store and state.travel:
        await asyncio.to_thread(travel_store.transition,state.store,state.owner_id,state.id,"released")
    state.status = "cancelled"
    emit = await _emit_for(state)
    await emit({"type": "cancelled", "message": "任务已被主动取消"})
    async with state.cond:
        state.cond.notify_all()
    await _persist_events(state)
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = -1):
    state = await _get_run_state(run_id, request)
    digest = web_auth.session_digest(request) if state.store else None

    async def gen():
        try:
            last=max(-1,after,int(request.headers.get("Last-Event-ID","-1")))
        except (ValueError,TypeError):
            last=max(-1,after)
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
                if state.store and not await asyncio.to_thread(state.store.session_user, digest):
                    break
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
async def download_report_md(run_id: str, request: Request = None):
    """合并该任务全部报告为单一 Markdown 文件，以附件形式触发浏览器下载。"""
    state = await _get_run_state(run_id, request)
    if state.store and state.status != "done":
        raise HTTPException(409, "研究完成后可下载报告。")
    if not state.reports:
        raise HTTPException(status_code=404, detail="报告尚未生成")

    parts = []
    for i, r in enumerate(state.reports, 1):
        md = (r.get("markdown") or "").strip()
        if not md and not state.store:
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


@app.get('/api/runs/{run_id}/report-chat')
async def get_report_chat(run_id: str, report_type: str, request: Request = None):
    state = await _get_run_state(run_id, request)
    try:
        text = await asyncio.to_thread(report_chat.report_text, state, report_type)
        return await report_chat.history(run_id, report_type, text, store=state.store)
    except report_chat.ChatError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except OSError:
        raise HTTPException(503, '本地报告暂时无法读取，请检查文件与目录权限。') from None


@app.post('/api/runs/{run_id}/report-chat')
async def ask_report_chat(run_id: str, req: report_chat.Question, request: Request = None):
    state = await _get_run_state(run_id, request)
    if not models.get_model(req.model_id):
        raise HTTPException(400, '所选问答模型不在可用列表中。')
    if model_unavailable(req.model_id):
        raise HTTPException(400, '该模型当前额度不可用，请更换模型。')
    if state.store:
        require_model_service()
        await web_auth.use_limit(state.store, "chat", state.owner_id, 86400,
                                 request.app.state.online_settings.max_daily_questions, "今日报告追问次数已达上限。")
    try:
        text = await asyncio.to_thread(report_chat.report_text, state, req.report_type)
        return await report_chat.ask(run_id, req.report_type, text, req, store=state.store)
    except report_chat.ChatError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except OSError:
        raise HTTPException(503, '本地报告暂时无法读取，请检查文件与目录权限。') from None


@app.get("/api/reports/{run_id}/{filename}")
async def get_report_file(run_id: str, filename: str, request: Request = None):
    state = await _get_run_state(run_id, request)

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

    if state.store:
        if state.status != "done" or not any(Path(r["path"]).name == filename for r in state.reports if r.get("path")):
            raise HTTPException(404, "报告文件不存在")
        content = await asyncio.to_thread(state.store.read_artifact, run_id, "reports", filename)
        if content is None:
            raise HTTPException(404, "报告文件不存在")
        return JSONResponse({"filename": filename, "content": content})
    p = (config.REPORT_DIR / filename).resolve()
    if config.REPORT_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if p not in {Path(r['path']).resolve() for r in state.reports if r.get('path')}:
        raise HTTPException(status_code=404,detail='文件不属于该任务')

    content = await asyncio.to_thread(p.read_text, encoding="utf-8")
    return JSONResponse({"filename": p.name, "content": content})


def main():
    if not web_auth.online_enabled():
        config.check_key()
        _restore_history_from_disk()
    config.logger.info(f"OSINT 多智能体服务启动：http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
