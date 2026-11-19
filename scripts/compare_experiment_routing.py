"""固定任务路由对照与固定证据补查对照。结果写入实验输出目录。"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from statistics import median
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import pipeline
from execution_policy import choose_policy
from research_routing import classify
from pipeline_execution import close_agent
import sag_evidence
from schemas import EvidenceItem, SubQuestion


ROUTING_CASES = [
    ("quick", "用一句话说明 URL 中的片段标识符做什么。"),
    ("quick", "简单比较 HTTP 301 和 302 的区别，只要两句话。"),
    ("quick", "深入一点解释论文中的消融实验是什么，限定一段话。"),
    ("quick", "只回答 Python 的 FastAPI 项目是用什么编程语言写的。"),
    ("standard", "整理某市三年公交客运量的变化，并说明统计口径。"),
    ("standard", "比较 SQLite 与 PostgreSQL 的并发、部署和维护差异。"),
    ("standard", "深入排查这个服务的三种超时原因，给每一种排查步骤。"),
    ("standard", "解释政府开放数据平台的覆盖范围、更新频率与数据缺口。"),
    ("deep", "重建一次公开云故障的时间线，交叉核验客户投诉、厂商公告和第三方监测的差异。"),
    ("deep", "决定是否采购联网检索服务，核查多家供应商的隐私、许可证、限流和账单风险。"),
    ("deep", "对照官方通报、现场报道和学术研究，评估三地洪灾响应并核查相互冲突的损失数字。"),
    ("deep", "全面研究某供应链安全事件，核验攻击链、受影响组件、缓解措施及各方说法的冲突。"),
]


async def routing_comparison():
    rows = []
    for expected, task in ROUTING_CASES:
        started = monotonic()
        tokens = None

        async def emit(event):
            nonlocal tokens
            if event["type"] == "model_response":
                tokens = event["usage"]["total"]

        try:
            route = await asyncio.wait_for(classify(pipeline, task, emit, close_agent), 12)
            choice = route.mode
        except Exception:
            choice = None
        rule = choose_policy(task).mode
        rows.append(dict(task=task, expected=expected, rule=rule, model=choice, chosen=choice or rule,
                         tokens=tokens, seconds=round(monotonic() - started, 3)))
    return dict(rows=rows, rule_correct=sum(r["rule"] == r["expected"] for r in rows),
                semantic_correct=sum(r["model"] == r["expected"] for r in rows),
                with_fallback_correct=sum(r["chosen"] == r["expected"] for r in rows),
                missing_usage_count=sum(r["tokens"] is None for r in rows),
                token_total=sum(r["tokens"] or 0 for r in rows),
                median_seconds=median(r["seconds"] for r in rows))


async def sag_comparison():
    scope_config = sag_evidence.settings(config)
    if not scope_config:
        raise SystemExit("请将 OSINT_SAG_SCOPE 设置为已建索引的 SAG Wiki 合成样例目录。")
    index, scope = scope_config
    cases = [
        ("主入口什么时候恢复开放？", "主入口", "2026-11-15"),
        ("西区步道属于开放范围吗？", "西区步道", "不属于此次开放范围"),
        ("西区接驳站是否运行？", "西区站", "西区站暂停使用"),
        ("开放通知适用于哪座景区？", "蓝松景区", "蓝松景区"),
    ]
    questions = [SubQuestion(question=question, search_queries=[query]) for question, query, _ in cases]
    web = [
        EvidenceItem(subquestion=question, source=f"https://{site}.example/{index}",
                     title=question, content=content, provider="fixture")
        for index, question, content in [
            (0, cases[0][0], "主入口从 2026-11-15 开始恢复开放，此安排按景区公开公告执行。"),
            (3, cases[3][0], "开放通知适用于蓝松景区，游客出发前应以现场公告为准。"),
        ]
        for site in ["official", "news"]
    ]
    web[1].content = "主入口恢复日期为 2026-11-15，售票系统显示该日期起有新的入园时段。"
    web[3].content = "蓝松景区适用本次开放通知，交通接驳的实际运行情况由现场公示。"
    scores = {}
    for arm, selected in [("web", []), ("all", questions), ("targeted", sag_evidence.targets(web, questions))]:
        evidence = list(web)
        started = monotonic()
        for question in selected:
            items = await asyncio.wait_for(sag_evidence.search(index, scope, question, "comparison", config.PORT), 8)
            evidence = sag_evidence.merge(evidence, items)
        scores[arm] = dict(queries=len(selected), local_hits=len(evidence)-len(web),
                           supported=sum(any(row.subquestion == question and expected in row.content
                                             for row in evidence) for question, _, expected in cases),
                           seconds=round(monotonic() - started, 3))
    return scores


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["routing", "sag"])
    args = parser.parse_args()
    config.enable_experiment()
    result = {}
    if args.only != "sag":
        config.check_key()
        result["routing"] = await routing_comparison()
    if args.only != "routing":
        result["sag"] = await sag_comparison()
    path = config.OUTPUT_DIR / f"depth-and-sag-{args.only or 'combined'}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
