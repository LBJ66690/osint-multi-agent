# agents.py -- 百炼客户端、MCP 工具（可观测）、五个智能体、结构化运行助手
from __future__ import annotations

import json
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from agent_framework import Agent, ContextWindowCompactionStrategy, FunctionTool, MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatClient
import httpx
from openai import AsyncOpenAI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import config
import local_knowledge
import models
from schemas import Analysis, EvidenceItem, Knowledge, ResearchPlan

# 事件发射器：async 回调，把事件推给上层（WebSocket/SSE/日志）
Emitter = Callable[[dict], Awaitable[None]]


# ---------- 结果文本抽取 ----------
def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            parts.append(_content_text(c))
        return "\n".join(parts)
    if hasattr(content, "text") and isinstance(getattr(content, "text"), str):
        return content.text
    return str(content)


def _preview(value: Any, limit: int = 600) -> str:
    text = _content_text(value)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _compact_result(value: Any, max_chars: int = 1600) -> str:
    """压缩工具结果再回填给模型：去空白、截断正文，但保留标题/来源/要点。"""
    text = _content_text(value)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（网页正文较长，已截断；引用请以标题与来源为准）"
    return text


def search_result_count(value: Any) -> int | None:
    """只统计明确的网页列表；无法识别时保持未知，不能误报为零。"""
    if isinstance(value, dict):
        for key in ("pages", "results"):
            rows = value.get(key)
            if isinstance(rows, list):
                return len(rows)
        for key in ("structuredContent", "content"):
            if key in value:
                count = search_result_count(value[key])
                if count is not None:
                    return count
        if isinstance(value.get("text"), str):
            return search_result_count(value["text"])
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return search_result_count(parsed)
    elif isinstance(value, list):
        counts = [search_result_count(item) for item in value]
        known = [count for count in counts if count is not None]
        return sum(known) if known else None
    else:
        for attr in ("structuredContent", "content", "text"):
            data = getattr(value, attr, None)
            if data is not None:
                count = search_result_count(data)
                if count is not None:
                    return count
    return None


# ---------- 可观测 MCP 工具（在调用前后发事件，供前端展示工具调用） ----------
class ObservableMCPStreamableHTTPTool(MCPStreamableHTTPTool):
    _emitter: Optional[Emitter] = None
    _max_calls: int = 18
    _max_result_chars: int = 1600
    _call_count: int = 0

    def __init__(
        self,
        *args,
        emitter: Optional[Emitter] = None,
        max_calls: int = 18,
        max_result_chars: int = 1600,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._emitter = emitter
        self._max_calls = max_calls
        self._max_result_chars = max_result_chars
        self._call_count = 0

    async def call_tool(self, tool_name: str, **kwargs):
        # 硬限流：达到上限后不再真正搜索，引导模型基于已有资料汇总，避免空转烧 token
        if self._call_count >= self._max_calls:
            if self._emitter:
                await self._emitter(
                    {"type": "tool_error", "stage": "collecting", "tool": tool_name,
                     "error": f"已达搜索上限 {self._max_calls} 次", "call_index": self._call_count}
                )
            return (
                f"搜索次数已达上限（{self._max_calls} 次）。请不要再调用搜索，"
                "直接基于上面已收集到的资料汇总输出 JSON；确实没有资料的子问题，"
                "在 content 中如实标注“未检索到”，不要编造。"
            )
        self._call_count += 1
        call_id = uuid.uuid4().hex[:12]
        call_index = self._call_count
        if self._emitter:
            await self._emitter(
                {"type": "tool_call", "stage": "collecting", "tool": "bailian", "tool_name": tool_name,
                 "args": kwargs, "call_index": call_index, "call_id": call_id}
            )
        try:
            result = await super().call_tool(tool_name, **kwargs)
            compact = _compact_result(result, self._max_result_chars)
            if self._emitter:
                await self._emitter(
                    {
                        "type": "tool_result",
                        "stage": "collecting",
                        "tool": "bailian", "tool_name": tool_name, "call_id": call_id,
                        "preview": _preview(result),
                        "result_count": search_result_count(result),
                        "call_index": call_index,
                    }
                )
            # 回填给模型的是“截断后的短文本”，而非整篇网页原文
            return compact
        except Exception as e:
            if self._emitter:
                await self._emitter(
                    {"type": "tool_error", "stage": "collecting", "tool": "bailian", "tool_name": tool_name, "call_id": call_id, "error": str(e)}
                )
            raise


# ---------- 本地工具（进程内 FunctionTool，避免沙箱内 stdio 子进程被禁止） ----------
class ReportTemplateInput(BaseModel):
    report_type: str = Field(description="报告类型，例如：舆情分析、资政建议")


class BriefingInput(BaseModel):
    topic: str = Field(description="查询的主题或地名，例如：贵阳花溪区")


async def _emit_local_call(emitter, tool: str, kwargs: dict):
    if emitter:
        await emitter({"type": "tool_call", "stage": "reporting", "tool": tool, "args": kwargs})


async def _emit_local_result(emitter, tool: str, result):
    if emitter:
        await emitter({"type": "tool_result", "stage": "reporting", "tool": tool, "preview": _preview(result)})


def make_local_tools(emitter: Optional[Emitter] = None) -> list:
    """返回进程内本地工具（报告模板 + 本地情报），可观测调用。"""

    async def _get_report_template(**kwargs):
        tool = "get_report_template"
        await _emit_local_call(emitter, tool, kwargs)
        result = local_knowledge.get_report_template(kwargs.get("report_type", ""))
        await _emit_local_result(emitter, tool, result)
        return result

    async def _get_local_briefing(**kwargs):
        tool = "get_local_briefing"
        await _emit_local_call(emitter, tool, kwargs)
        result = local_knowledge.get_local_briefing(kwargs.get("topic", ""))
        await _emit_local_result(emitter, tool, result)
        return result

    return [
        FunctionTool(
            name="get_report_template",
            description="获取指定类型报告的写作模板。",
            input_model=ReportTemplateInput,
            func=_get_report_template,
        ),
        FunctionTool(
            name="get_local_briefing",
            description="查询本地/内部情报知识（由分析人员事先录入）。",
            input_model=BriefingInput,
            func=_get_local_briefing,
        ),
    ]


# ---------- 百炼聊天客户端（OpenAI 兼容） ----------
def make_chat_client(model_id: str | None = None, request_timeout: float = 85) -> OpenAIChatClient:
    return OpenAIChatClient(
        model=config.resolve_model(model_id),
        async_client=AsyncOpenAI(api_key=config.DASHSCOPE_API_KEY, base_url=config.BASE_URL,
                                 timeout=request_timeout, max_retries=0),
    )


def build_agent(model_id: str, instructions: str, name: str, tools=None, cap_output: bool = True,
                role: str = "", mode: str = "standard", output_tokens: int | None = None):
    """构造带 harness 能力的智能体：注入上下文压缩与输出上限。

    根据岗位与研究深度限制输出和思考强度。
    cap_output 参数保留兼容旧调用；所有岗位均受输出预算约束。
    """
    m = models.get_model(model_id) or {}
    ctx = int(m.get("context_window", config.CONTEXT_WINDOW_TOKENS))
    out = output_tokens or min(int(m.get("max_output_tokens", config.MAX_OUTPUT_TOKENS)), 4000)
    kwargs: dict[str, Any] = {
        "instructions": instructions,
        "name": name,
        "compaction_strategy": ContextWindowCompactionStrategy(
            max_context_window_tokens=ctx,
            max_output_tokens=out,
        ),
    }
    kwargs["default_options"] = models.role_options(role, model_id, mode, out)
    if tools is not None:
        kwargs["tools"] = tools
    from execution_policy import choose_policy
    request_timeout = choose_policy("", mode).timeout_for(role) + 10
    return Agent(make_chat_client(model_id, request_timeout=request_timeout), **kwargs)


# ---------- MCP 工具工厂 ----------
def make_websearch_mcp(emitter: Optional[Emitter] = None) -> ObservableMCPStreamableHTTPTool:
    # 远程 MCP：百炼联网搜索（Streamable HTTP，Bearer 鉴权）
    return ObservableMCPStreamableHTTPTool(
        name="bailian-websearch",
        url=config.WEBSEARCH_MCP_URL,
        static_headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
        description="阿里云百炼联网搜索：检索网页、新闻、社媒公开信息。",
        emitter=emitter,
        max_calls=config.SEARCH_MAX_CALLS,
        max_result_chars=config.SEARCH_RESULT_MAX_CHARS,
    )


def _robust_http_factory(headers=None, timeout=None, auth=None):
    """带连接重试与长超时的 httpx 客户端，抵御瞬时 TLS/连接超时与死连接复用。"""
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=10.0)
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(90.0, connect=30.0),
        auth=auth,
        limits=limits,
        transport=httpx.AsyncHTTPTransport(retries=3),
    )


@asynccontextmanager
async def websearch_mcp_session(emitter: Optional[Emitter] = None):
    """用原生 mcp 建立并握手百炼 WebSearch，再把“已连接会话”注入 MAF 工具。"""
    headers = {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"}
    async with streamablehttp_client(
        config.WEBSEARCH_MCP_URL,
        headers=headers,
        timeout=90,
        httpx_client_factory=_robust_http_factory,
    ) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool = ObservableMCPStreamableHTTPTool(
                name="bailian-websearch",
                url=config.WEBSEARCH_MCP_URL,
                session=session,  # 注入已握手会话，MAF 不再自建 transport
                load_tools=True,
                description="阿里云百炼联网搜索：检索网页、新闻、社媒公开信息。",
                emitter=emitter,
                max_calls=config.SEARCH_MAX_CALLS,
                max_result_chars=config.SEARCH_RESULT_MAX_CHARS,
            )
            await tool.load_tools()
            yield tool


# ---------- 各智能体的岗位职责（instructions） ----------
PLANNER_INSTR = """你是开源情报分析的【规划官】。
职责：理解用户真实目标，把任务拆解成若干可检索的子问题，并为每个子问题给出 1~3 条搜索关键词；
同时判断用户最终需要哪类报告（如舆情分析、资政建议）。
若任务涉及国际科技或学术，在每个子问题的搜索关键词中提供一个简短英文词组，便于检索英文公开接口。
只输出 JSON，不要解释。"""

COLLECTOR_INSTR = """你是开源情报分析的【收集官】，你可以使用百炼联网搜索工具。
职责：针对每一个子问题，使用其搜索关键词调用联网搜索工具，收集真实、公开、尽量新的资料；
每条资料都要保留来源（URL 或平台名），并提炼要点。若搜不到，如实在 content 中标注。
搜索纪律：每个子问题最多搜索 2 次；优先使用给定关键词，若结果已足够就不要换词重搜，避免重复检索。
最终把所有子问题的资料汇总输出为 JSON，不要解释。"""

COLLECTOR_INSTR_ONE = """你是开源情报分析的【收集官】，你可以使用百炼联网搜索工具。
职责：针对下面这单个子问题，使用其搜索关键词调用联网搜索工具，收集真实、公开、尽量新的资料；
每条资料都要保留来源（URL 或平台名），并提炼要点。若搜不到，如实在 content 中标注。
搜索纪律：本子问题最多搜索 2 次；优先使用给定关键词，若结果已足够就不要换词重搜，避免重复检索。
把本子问题的资料汇总输出为 JSON，不要解释。"""

ANALYST_INSTR = """你是开源情报分析的【分析官】。
职责：对收集到的资料做清洗（去重、纠错、补全、丢弃无关内容）；
抽取实体并做归一化，处理“一义多词/同义别名”（例如：黔=贵州，花溪=花溪区）；
归纳各方围绕各话题的观点与情感倾向（正面/中性/负面），并保留具体例子。
只输出 JSON，不要解释。"""

REASONER_INSTR = """你是开源情报分析的【推理官】。
职责：在结构化的观点与实体基础上做归纳推理，形成若干条核心发现；
每条发现都要列出：支持它的证据、反证或不同声音、来源，做到可信、可解释、有具体例子。
只输出 JSON，不要解释。"""

REPORTER_INSTR = """你是开源情报分析的【报告官】，你可以使用本地 MCP 工具。
职责：先调用 get_report_template 获取指定类型的报告模板（必要时调用 get_local_briefing 获取本地情报），
然后严格按照模板结构、结合给定的分析发现撰写中文报告。
报告必须引用具体例子和来源，区分事实与判断。
直接输出报告正文（Markdown），不要输出 JSON。"""

VERIFIER_INSTR = """你是开源情报流水线的【核查官】，担任每个阶段产出的质量关卡与独立第三方质检。
职责：按用户消息中给出的核查标准，把该阶段产出逐条对照参照材料核查，输出关卡判定：
- pass：产出达标，可放行进入下一阶段（issues 为空列表，summary 给出结论）；
- fail：存在必须修正的问题，需打回重做（issues 列明问题，fail 时 issues 不能为空；按提示要求填写 redo_targets）。
核查时区分：陈述是否有证据支撑、是否把推测写成事实、是否偏离研究目标。
只输出 JSON，不要解释。"""


# 多模型分工：不同岗位用不同模型（兼顾质量、速度与成本）
ROLE_MODELS = models.ROLE_DEFAULTS


def resolve_role_models(preferred: str | None = None) -> dict:
    role_models = dict(ROLE_MODELS)
    # 岗位覆盖由显式 role_models 提交，不能让旧的全局模型悄悄覆盖推荐编排。
    return role_models


def make_agents(web_mcp, local_tools, preferred: str | None = None, role_models: dict | None = None):
    if role_models is None:
        role_models = resolve_role_models(preferred)

    planner = Agent(
        make_chat_client(role_models["planner"]),
        instructions=PLANNER_INSTR, name="规划官",
    )
    collector = Agent(
        make_chat_client(role_models["collector"]),
        instructions=COLLECTOR_INSTR, name="收集官", tools=web_mcp,
    )
    analyst = Agent(
        make_chat_client(role_models["analyst"]),
        instructions=ANALYST_INSTR, name="分析官",
    )
    reasoner = Agent(
        make_chat_client(role_models["reasoner"]),
        instructions=REASONER_INSTR, name="推理官",
    )
    reporter = Agent(
        make_chat_client(role_models["reporter"]),
        instructions=REPORTER_INSTR, name="报告官", tools=local_tools,
    )
    return planner, collector, analyst, reasoner, reporter


# ---------- 收集官输出是“数组”，包一层对象以兼容 JSON 模式 ----------
class EvidenceList(BaseModel):
    items: list[EvidenceItem]


# ---------- 结构化运行助手 ----------
def _extract_json(text: str) -> str:
    """从大模型原始回复中稳健剥离出 JSON 对象文本（自动处理代码块包裹与前后缀说明）"""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            t = "\n".join(lines[1:-1]).strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t


def text_of(resp) -> str:
    return getattr(resp, "text", None) or str(resp)


def _extract_usage(resp) -> dict:
    """从 MAF 运行结果里抠出 token 用量；拿不到就返回 0。"""
    ud = getattr(resp, "usage_details", None)
    if isinstance(ud, dict):
        pt = ud.get("input_token_count") or ud.get("prompt_tokens") or 0
        ct = ud.get("output_token_count") or ud.get("completion_tokens") or 0
        tt = ud.get("total_token_count") or (pt + ct) or 0
        return {"prompt": int(pt or 0), "completion": int(ct or 0), "total": int(tt or 0)}
    for attr in ("usage", "_usage"):
        u = getattr(resp, attr, None)
        if not u:
            continue
        try:
            d = u if isinstance(u, dict) else {
                k: getattr(u, k, None) for k in
                ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")
            }
            pt = d.get("prompt_tokens") or d.get("input_tokens") or 0
            ct = d.get("completion_tokens") or d.get("output_tokens") or 0
            tt = d.get("total_tokens") or (pt + ct) or 0
            return {"prompt": int(pt or 0), "completion": int(ct or 0), "total": int(tt or 0)}
        except Exception:
            pass
    return {"prompt": 0, "completion": 0, "total": 0}


def _agent_model_id(agent) -> str:
    """从智能体绑定的聊天客户端取模型 id（未知时返回空串）。"""
    opts = getattr(agent, "default_options", None) or {}
    return opts.get("model") or getattr(getattr(agent, "client", None), "model", "") or ""


def _model_supports_temperature(model_id: str) -> bool:
    """查模型清单：是否接受 temperature 参数（kimi-k3 等模型会直接 400）。"""
    m = models.get_model(model_id or "")
    return m.get("supports_temperature", True) if m else True


_SCHEMA_CACHE: dict[type, str] = {}


def _output_contract(schema: type[BaseModel]) -> str:
    """使用真实字段层级的模板，说明单独列出，不包装业务字段。"""
    definition = schema.model_json_schema()
    notes = []
    def template(node, path='$'):
        if '$ref' in node:
            node = definition['$defs'][node['$ref'].rsplit('/', 1)[-1]]
        if node.get('description'):
            notes.append(path + '：' + node['description'])
        if node.get('type') == 'object' and 'properties' in node:
            required = node.get('required', [])
            notes.append(path + ' 必填字段：' + '、'.join(required))
            return {name: template(value, path+'.'+name) for name, value in node['properties'].items()}
        if node.get('type') == 'array':
            return [template(node.get('items', {}), path+'[]')]
        if node.get('type') == 'object':
            return {'<填写:实际键>': template(node.get('additionalProperties', {}), path+'.*')}
        if 'enum' in node:
            notes.append(path+' 仅允许：'+json.dumps(node['enum'], ensure_ascii=False))
        return '<填写:'+path+'>'
    shape = template(definition)
    return (json.dumps(shape, ensure_ascii=False, indent=2) + '\n字段说明（不属于返回对象）：\n'
            + '\n'.join(notes) + '\n把所有 <填写:...> 替换为真实值；没有依据的可选列表/映射使用 []/{}。'
            '数组中直接放模板所示元素，不增加 entity、填写、必填等包装层。')


def _reject_schema_echo(clean: str) -> None:
    data = json.loads(clean)
    if isinstance(data, dict) and ('$defs' in data or '$schema' in data or
            (data.get('type') == 'object' and isinstance(data.get('properties'), dict))):
        raise ValueError('返回了 JSON Schema 定义而非业务结果。请根据证据填写实际字段值，禁止复述结构定义；不得凭空判定通过。')
    def has_placeholder(value):
        if isinstance(value, str): return value.startswith('<填写:') and value.endswith('>')
        if isinstance(value, dict): return any(has_placeholder(k) or has_placeholder(v) for k,v in value.items())
        if isinstance(value, list): return any(has_placeholder(v) for v in value)
        return False
    if has_placeholder(data):
        raise ValueError('返回了字段模板占位符，请填写实际证据支持的内容；没有依据的可选列表使用空列表。')


def _json_error_hint(error):
    if isinstance(error, json.JSONDecodeError):
        return (f'JSON 语法错误：第 {error.lineno} 行第 {error.colno} 列（字符 {error.pos}），{error.msg}。'
                '检查字符串中的双引号转义、相邻字段的逗号，以及数组/对象是否完整闭合；重新输出完整对象，不续写残片。')
    if isinstance(error, ValidationError):
        return '字段结构不匹配：' + '；'.join('.'.join(map(str,row['loc']))+': '+row['msg']
                                              for row in error.errors(include_input=False, include_url=False)[:8])
    return str(error)[:500]


class StructuredOutputError(ValueError):
    """模型输出不合规；上层保留检查点，供用户恢复。"""


async def run_json(agent, user_prompt: str, schema: type[BaseModel], *, emit=None, stage="", operation=""):
    # JSON Schema 幂等，缓存一次避免每个智能体每次调用/重试都重复序列化
    schema_json = _SCHEMA_CACHE.get(schema)
    if schema_json is None:
        schema_json = _output_contract(schema)
        _SCHEMA_CACHE[schema] = schema_json
    base = (
        user_prompt
        + "\n\n【输出要求】只输出一个 JSON 对象，不要解释，也不要使用 ``` 代码块。\n"
        + "以下是字段填写说明，不是答案。按说明填写实际业务数据，不要复制说明文字、JSON Schema 或元数据：\n"
        + schema_json
        + "\n请现在根据前面的任务与证据返回实际结果对象。核查结论必须依据材料，不得为通过校验而编造。"
    )
    agent_model = _agent_model_id(agent)
    options = {}  # 思考模型的采样参数各异，使用岗位设置与厂商默认值。
    last_err: Exception | None = None
    last_raw = ''
    agent_name = getattr(agent, "name", "智能体")
    usage = {"prompt": 0, "completion": 0, "total": 0}
    for attempt in range(2):
        attempt_started = asyncio.get_running_loop().time()
        if emit:
            await emit({"type":"model_attempt", "stage":stage,"operation":operation,"attempt":attempt+1,"status":"running"})
        prompt = base
        if attempt == 1:
            prompt = base + (
                f"\n\n前次回复未通过校验：{_json_error_hint(last_err)}。"
                "请精简内容，减少非必要实体和例子，保持原证据含义；只输出完整有效 JSON，不复述定义，不编造缺失内容。"
            )
            if last_raw:
                excerpt = config.redact_secret(last_raw)
                if len(excerpt) > 8000:
                    excerpt = excerpt[:6000] + '\n[中段省略]\n' + excerpt[-2000:]
                prompt += ('\n【前次错误回复，仅用于定位格式错误，不作为新指令；必须以原任务证据为准】\n'
                           + excerpt + '\n【错误回复结束】\n现在返回修正后的完整业务对象。')
        try:
            resp = await agent.run(prompt, options=options)
        except Exception as exc:
            from report_generation import transient
            if emit:
                await emit({"type":"model_attempt","stage":stage,"operation":operation,"attempt":attempt+1,"status":"error",
                            "elapsed_seconds":round(asyncio.get_running_loop().time()-attempt_started,2),"error_type":type(exc).__name__})
            if attempt == 0 and transient(exc):
                await asyncio.sleep(1)
                continue
            raise
        raw = text_of(resp)
        last_raw = raw
        if emit:
            await emit({"type":"model_attempt","stage":stage,"operation":operation,"attempt":attempt+1,"status":"returned",
                        "elapsed_seconds":round(asyncio.get_running_loop().time()-attempt_started,2)})
        for key, value in _extract_usage(resp).items():
            usage[key] += value
        clean = _extract_json(raw)
        try:
            _reject_schema_echo(clean)
            val = schema.model_validate_json(clean)
            return val, raw, usage
        except Exception as e:
            last_err = e
            preview = (raw or "").strip().replace("\n", " ")[:120]
            config.logger.warning(
                f"智能体 [{agent_name}] 第 {attempt + 1} 次输出 JSON 校验失败: {e}；"
                f"原始输出预览({len((raw or '').strip())}字符): {preview!r}"
            )
    config.logger.error(f"智能体 [{agent_name}] 两次尝试仍无法输出合规 JSON: {last_err}")
    raise StructuredOutputError(f'{agent_name}连续两次返回不合规的结构化结果。{_json_error_hint(last_err)} '
                                '本次未生成有效产出，已保留之前成功的阶段，可恢复重试或更换该岗位模型。') from last_err
