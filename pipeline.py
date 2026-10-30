# pipeline.py -- 主编排：规划 -> 人工确认 -> 收集(MCP) -> 分析 -> 推理 -> 报告
# 支持即时落盘（Checkpointing）、断点续传（Resume）、按需MCP会话管理与全链路日志
from __future__ import annotations

import asyncio
import json
import traceback
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

import config
import models
import search_sources
from report_generation import generate_report
from agents import (
    ANALYST_INSTR,
    COLLECTOR_INSTR_ONE,
    PLANNER_INSTR,
    REASONER_INSTR,
    REPORTER_INSTR,
    VERIFIER_INSTR,
    EvidenceList,
    _extract_usage,
    build_agent,
    make_local_tools,
    resolve_role_models,
    run_json,
    text_of,
    websearch_mcp_session,
)
from schemas import Analysis, EvidenceItem, Knowledge, ResearchPlan, VerificationReport

Emitter = Callable[[dict], Awaitable[None]]
ConfirmFn = Callable[[dict], Awaitable[str]]
GateFn = Callable[[dict], Awaitable[dict]]  # 关卡决策：返回 {"decision": "redo"/"continue", "feedback": str}
SourceFn = Callable[[dict], Awaitable[list[str]]]

# 每个阶段最多人工打回重做的次数（超过自动放行，防止无限循环）
MAX_GATE_REDO = 2

# 各阶段核查标准（拼入核查官用户消息）
GATE_CRITERIA = {
    "planning": (
        "对照【用户任务】核查研究计划：\n"
        "1) goal 是否准确复述用户目标；\n"
        "2) 子问题是否覆盖目标、每条都配有可检索的关键词；\n"
        "3) report_types 是否匹配任务意图。\n"
        "若存在明显缺口或不可执行，判 fail。"
    ),
    "collecting": (
        "对照【研究计划】核查证据充分性：\n"
        "1) 每个子问题是否都有资料；\n"
        "2) 资料是否与研究目标相关、没有大面积离题；\n"
        "3) 来源是否多样可靠。\n"
        "若某些子问题完全无资料或资料全部离题，判 fail，并在 redo_targets 中原样列出证据不足的子问题。"
    ),
    "analyzing": (
        "对照【证据清单】核查结构化知识：\n"
        "1) 观点是否有证据支撑（不得出现证据中不存在的内容）；\n"
        "2) 别名与实体是否来自证据；\n"
        "3) 情感倾向判断是否有依据。"
    ),
    "reasoning": (
        "对照【结构化知识】核查核心发现：\n"
        "1) 每条发现是否有支持证据；\n"
        "2) 是否考虑了反证与不同声音；\n"
        "3) 结论是否为证据支撑的合理推断、没有过度推断。"
    ),
    "reporting": (
        "对照【核心发现】与【证据清单】核查报告正文：\n"
        "1) 关键陈述是否有出处；\n"
        "2) 是否把推测写成事实；\n"
        "3) 是否回答了研究主题。\n"
        "若判 fail，在 redo_targets 中原样列出需要重写的报告类型。"
    ),
}

GATE_STAGE_LABEL = {
    "planning": "规划官",
    "collecting": "收集官",
    "analyzing": "分析官",
    "reasoning": "推理官",
    "reporting": "报告官",
}


def _gate_prompt(gate_name: str, output_desc: str, output_str: str, reference_desc: str, reference_str: str) -> str:
    """构造某个阶段的质量关卡核查提示词。"""
    return (
        f"【{GATE_STAGE_LABEL[gate_name]}产出核查】\n{GATE_CRITERIA[gate_name]}\n\n"
        f"【{output_desc}】\n{output_str}\n\n"
        f"【{reference_desc}】\n{reference_str}\n\n"
        "请给出关卡判定与问题清单。"
    )


def _fmt_gate_feedback(check: "VerificationReport", user_fb: str) -> str:
    """把核查问题清单（+用户意见）格式化为打回重做的反馈文本。"""
    lines = ["上一轮产出未通过核查官核查，问题清单："]
    for i, iss in enumerate(check.issues, 1):
        lines.append(f"{i}. [{iss.verdict}/{iss.severity}] {iss.claim} —— {iss.explanation}")
    if check.summary:
        lines.append(f"核查总评：{check.summary}")
    if user_fb:
        lines.append(f"用户补充意见：{user_fb}")
    return "\n".join(lines)


STAGES = [
    ("planning", "规划", 8),
    ("collecting", "收集", 40),
    ("analyzing", "分析", 60),
    ("reasoning", "推理", 75),
    ("reporting", "报告", 90),
    ("verifying", "核查", 100),
]

STAGE_ROLE = {
    "planning": "planner",
    "collecting": "collector",
    "analyzing": "analyst",
    "reasoning": "reasoner",
    "reporting": "reporter",
    "verifying": "verifier",
}


def get_checkpoint_path(run_id: str) -> Path:
    """获取指定任务的断点快照路径"""
    return config.CHECKPOINT_DIR / f"ckpt_{run_id}.json"


async def _awrite(path: Path, text: str) -> None:
    """异步写文件：把磁盘 I/O 移出事件循环，避免阻塞并发协程。"""
    await asyncio.to_thread(path.write_text, config.redact_secret(text), encoding="utf-8")


async def save_checkpoint(run_id: str, data: dict) -> None:
    """即时将阶段数据持久化至检查点文件，确保崩溃不丢数据"""
    if not run_id:
        return
    try:
        p = get_checkpoint_path(run_id)
        snapshot = json.dumps(config.redact_data(data), ensure_ascii=False, indent=2)
        temporary = p.with_name(p.name + "." + uuid.uuid4().hex[:8] + ".tmp")
        await _awrite(temporary, snapshot)
        await asyncio.to_thread(temporary.replace, p)
        config.logger.debug(f"[{run_id}] 检查点已保存，已完成阶段: {data.get('completed_stages', [])}")
    except Exception as e:
        config.logger.warning(f"[{run_id}] 保存检查点异常: {e}")
        raise RuntimeError("检查点保存失败，为避免丢失恢复进度已停止执行") from e


def load_checkpoint(run_id: str) -> dict | None:
    """读取指定任务的历史检查点数据"""
    if not run_id:
        return None
    try:
        p = get_checkpoint_path(run_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        config.logger.warning(f"[{run_id}] 读取检查点失败: {e}")
    return None


def _verification_appendix(report_type: str, v: VerificationReport) -> str:
    """把核查结论格式化为追加在报告末尾的「事实核查附录」Markdown。"""
    lines = [
        "\n\n---\n\n",
        f"## 附：事实核查（{report_type}）\n\n",
        f"**核查结论**：{v.summary}\n\n",
    ]
    if not v.issues:
        lines.append("本次核查未发现无出处、推测当事实或与证据冲突的陈述。\n")
        return "".join(lines)
    sev_order = {"高": 0, "中": 1, "低": 2}
    issues = sorted(v.issues, key=lambda i: sev_order.get(i.severity, 3))
    lines.append(f"共发现问题 {len(issues)} 条，按严重程度排序：\n\n")
    lines.append("| 被核查陈述 | 结论 | 核查依据 | 严重度 |\n|---|---|---|---|\n")
    for i in issues:
        claim = i.claim.replace("|", "\\|").replace("\n", " ")
        exp = i.explanation.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {claim} | {i.verdict} | {exp} | {i.severity} |\n")
    return "".join(lines)


async def run_pipeline(task, model_id, emit, confirm, run_id="", role_models_override=None,
                       resume=False, gate=None, select_sources=None, execution_mode="auto", search_strategy="legacy"):
    import sys
    from pipeline_execution import execute
    return await execute(sys.modules[__name__], task, model_id, emit, confirm, run_id,
                         role_models_override, resume, gate, select_sources, execution_mode, search_strategy)
