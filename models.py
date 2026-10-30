# models.py -- 模型清单（能力更强的新一代模型，全部经阿里云百炼兼容模式调用）
# 每个模型 = 一个 id（传给 API 的 model 字段）+ 展示用元数据。
# 端点与鉴权统一走 config 的兼容模式（DASHSCOPE_API_KEY）。
from __future__ import annotations

from typing import Optional

# tier：旗舰 > 高阶 > 均衡 > 轻量
MODELS: list[dict] = [
    {
        "id": "qwen3.8-2.4t-a95b",
        "label": "Qwen3.8 2.4T (A95B)",
        "brand": "通义千问",
        "tier": "旗舰",
        "desc": "超大参数旗舰，深度推理与分析能力最强，适合复杂研判与资政建议。",
        "recommended": False,
    },
    {
        "id": "qwen3.8-max",
        "label": "Qwen3.8 Max",
        "brand": "通义千问",
        "tier": "旗舰",
        "desc": "旗舰级综合能力，适合复杂任务；可能增加处理开销。",
        "recommended": False,
    },
    {
        "id": "deepseek-v4-pro-0813",
        "label": "DeepSeek V4 Pro (0813)",
        "brand": "DeepSeek",
        "tier": "旗舰",
        "desc": "推理与代码/数学能力强，结构化输出稳定，适合证据链推理。",
        "recommended": False,
    },
    {
        "id": "qwen3.8-27b",
        "label": "Qwen3.8 27B",
        "brand": "通义千问",
        "tier": "高阶",
        "desc": "27B 高阶模型，质量与速度兼顾，适合日常情报分析。",
        "recommended": False,
    },
    {
        "id": "kimi-k3",
        "label": "Kimi K3",
        "brand": "Kimi",
        "tier": "高阶",
        "desc": "长上下文与联网理解出色，适合多源资料整合。",
        "recommended": False,
        "supports_temperature": False,  # 百炼侧该模型不接受 temperature 参数
    },
    {
        "id": "glm-5.3",
        "label": "GLM-5.3",
        "brand": "智谱 GLM",
        "tier": "高阶",
        "desc": "中文理解与知识密度优秀，适合舆情观点归纳。",
        "recommended": False,
        # 强制思维链模型：reasoning tokens 计入输出预算；关卡打回重跑轮 prompt 更长（含核查反馈），
        # 思维链可达 16k+，16384 仍会被吃光导致正文截断成碎片，实测 DashScope 接受 32768
        "max_output_tokens": 32768,
    },
    {
        "id": "deepseek-v4-flash-0731",
        "label": "DeepSeek V4 Flash (0731)",
        "brand": "DeepSeek",
        "tier": "均衡",
        "desc": "均衡性价比，速度较快，适合批量子问题检索。",
        "recommended": False,
    },
    {
        "id": "deepseek-v4.1-flash-1017",
        "label": "DeepSeek V4.1 Flash (1017)",
        "brand": "DeepSeek", "tier": "均衡",
        "desc": "1017 快照版本，供标准及深入研究的资料分析使用。",
        "recommended": False,
    },
    {
        "id": "deepseek-v4.1-flash",
        "label": "DeepSeek V4.1 Flash",
        "brand": "DeepSeek",
        "tier": "均衡",
        "desc": "新一代快速模型，适合高并发收集与初筛。",
        "recommended": False,
    },
    {
        "id": "qwen3.8-flash",
        "label": "Qwen3.8 Flash",
        "brand": "通义千问",
        "tier": "轻量",
        "desc": "快速理解与生成，默认负责规划和最终核查。",
        "recommended": True,
    },
    {
        "id": "qwen3.7-flash-2026-07-15",
        "label": "Qwen3.7 Flash (0715)",
        "brand": "通义千问",
        "tier": "轻量",
        "desc": "上一代快速模型，稳定可靠，适合兜底。",
        "recommended": False,
    },
    {
        "id": "qwen3.7-flash",
        "label": "Qwen3.7 Flash",
        "brand": "通义千问",
        "tier": "轻量",
        "desc": "快速轻量模型，成本低，适合试跑与占位。",
        "recommended": False,
    },
]

# 上下文预算：按模型档位给出保守默认（非厂商权威值，可按官方文档覆盖）
# 用于 harness 的上下文压缩与输出上限，避免证据重灌撑爆上下文 / 思维链输出无节制膨胀。
_BUDGET = {
    "旗舰": (131072, 16384),
    "高阶": (131072, 8192),
    "均衡": (131072, 8192),
    "轻量": (131072, 4096),
}
for _m in MODELS:
    _ctx, _out = _BUDGET.get(_m.get("tier"), (131072, 8192))
    _m.setdefault("context_window", _ctx)
    _m.setdefault("max_output_tokens", _out)


def get_model(model_id: str) -> Optional[dict]:
    for m in MODELS:
        if m["id"] == model_id:
            return m
    return None


def default_model_id() -> str:
    rec = next((m for m in MODELS if m.get("recommended")), None)
    return rec["id"] if rec else MODELS[0]["id"]


TIER_ORDER = {"旗舰": 0, "高阶": 1, "均衡": 2, "轻量": 3}

# 推荐来自官方能力/参数说明，并由本项目的小请求验证兼容性；并非速度排行榜。
ROLE_DEFAULTS = {
    "planner": "qwen3.8-flash", "collector": "deepseek-v4.1-flash",
    "analyst": "deepseek-v4.1-flash", "reasoner": "deepseek-v4.1-flash",
    "reporter": "qwen3.8-27b", "verifier": "qwen3.8-flash",
}
ROLE_REASONS = {
    "planner": "快速理解目标、生成短计划，关闭额外思考，避免简单问题过度拆解。",
    "collector": "仅选择百炼搜索时使用；免费 MCP 直接检索，不调用收集模型。",
    "analyst": "普通任务关闭额外思考，抽取证据中的观点；深入研究使用低强度思考。",
    "reasoner": "低强度证据推理；深入研究才提高思考强度。",
    "reporter": "适度篇幅的中文写作与来源整理，无需超大模型长时间推理。",
    "verifier": "与分析模型分工，重点检查最终结论与引用，避免逐阶段全文重读。",
}
ROLE_PROFILES = {
    "quick": dict(ROLE_DEFAULTS),
    "standard": {
        "planner": "qwen3.8-27b", "collector": "deepseek-v4.1-flash",
        "analyst": "deepseek-v4.1-flash-1017", "reasoner": "deepseek-v4.1-flash-1017",
        "reporter": "qwen3.8-27b", "verifier": "qwen3.8-27b",
    },
    "deep": {
        "planner": "qwen3.8-max", "collector": "deepseek-v4.1-flash-1017",
        "analyst": "deepseek-v4.1-flash-1017", "reasoner": "deepseek-v4-pro-0813",
        "reporter": "kimi-k3", "verifier": "qwen3.8-max",
    },
}
PROFILE_REASONS = {
    "quick": dict(ROLE_REASONS),
    "standard": {
        "planner": "27B 拆解多子问题，兼顾计划质量与处理开销。",
        "collector": ROLE_REASONS["collector"],
        "analyst": "1017 版本整理多源资料；普通分析关闭额外思考，优先结构化产出。",
        "reasoner": "1017 版本以低强度思考串联证据、结论与反证。",
        "reporter": "27B 按研究模板组织中文报告，控制篇幅。",
        "verifier": "27B 对照核心证据核查最终报告，与分析岗位采用不同模型。",
    },
    "deep": {
        "planner": "Max 规划复杂研究的范围、子问题和交付物。",
        "collector": ROLE_REASONS["collector"],
        "analyst": "1017 版本提炼较多证据，关闭额外思考以稳定输出结构化内容；复杂研判交由推理官。",
        "reasoner": "Pro 负责复杂证据链与反证研判，提高思考强度并保留阶段预算。",
        "reporter": "Kimi K3 整合长材料与研判结论，生成完整研究报告。",
        "verifier": "Max 独立核查关键事实与过度推断，避免分析模型自审。",
    },
}


def role_defaults(mode="standard"):
    return dict(ROLE_PROFILES.get(mode, ROLE_PROFILES["standard"]))

MODEL_SOURCES = [
    {"title": "阿里云：Qwen3.8 Flash", "url": "https://help.aliyun.com/zh/model-studio/qwen3-8-flash"},
    {"title": "阿里云：DeepSeek V4.1 Flash", "url": "https://help.aliyun.com/zh/model-studio/deepseek-v4-1-flash"},
    {"title": "阿里云：Qwen3.8 27B", "url": "https://help.aliyun.com/zh/model-studio/qwen3-8-27b"},
    {"title": "阿里云：Responses 思考强度", "url": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses"},
]


def role_options(role, model_id, mode="standard", max_tokens=4000):
    """当前客户端使用 Responses；reasoning.effort 是该协议的参数。"""
    effort = "high" if mode == "deep" and role == "reasoner" else "low"
    if role in {"analyst","verifier"} and model_id in {"qwen3.8-flash", "qwen3.8-max", "deepseek-v4.1-flash", "deepseek-v4.1-flash-1017", "qwen3.8-27b"}:
        effort = "none"
    if role in {"planner", "reporter", "collector"} and model_id in {"qwen3.8-flash", "qwen3.8-27b", "qwen3.8-max"}:
        effort = "none"
    options = {"max_tokens": max_tokens, "reasoning": {"effort": effort}}
    return options
