"""按任务范围配置工作量；数值是应用预算，不是模型厂商的能力上限。"""
from dataclasses import dataclass, asdict
import re


@dataclass(frozen=True)
class Policy:
    mode: str
    label: str
    questions: int
    queries: int
    reports: int
    evidence_items: int
    evidence_chars: int
    excerpt_chars: int
    active_seconds: int
    model_seconds: int
    report_seconds: int
    output_tokens: int
    report_words: int
    reasoning_seconds: int = 120

    def timeout_for(self, role):
        if role == "reasoner":
            return self.reasoning_seconds
        if role == "reporter":
            return self.report_seconds
        if role in {"planner", "verifier"}:
            return min(self.model_seconds, 60)
        return self.model_seconds

    def public(self):
        return asdict(self)


POLICIES = {
    "quick": Policy("quick", "简明解答", 1, 1, 1, 8, 10000, 900, 300, 75, 100, 2600, 800, 75),
    "standard": Policy("standard", "标准研究", 3, 1, 2, 16, 22000, 1100, 480, 100, 140, 4000, 1600),
    "deep": Policy("deep", "深入研究", 5, 2, 3, 24, 38000, 1400, 720, 150, 180, 6000, 2400, 360),
}


def choose_policy(task: str, requested: str = "auto") -> Policy:
    if requested not in {"auto", *POLICIES}:
        raise ValueError("未知研究深度")
    if requested != "auto":
        return POLICIES[requested]
    if re.search(r"深度|深入|全面|系统性|尽调|资政|医疗|诊断|投资决策|法律意见", task):
        return POLICIES["deep"]
    complex_task = re.search(r"对比|比较|调研|报告|方案|趋势|分别|以及|并且|与.*区别|和.*区别", task)
    simple = re.search(r"是什么|什么意思|指的是什么|是谁|什么梗|含义|解释一下|what is|who is", task, re.I)
    return POLICIES["quick" if len(task) <= 160 and simple and not complex_task else "standard"]


def plan_instruction(policy):
    return (f"执行深度：{policy.label}。最多 {policy.questions} 个子问题，每题 {policy.queries} 条精确查询；"
            f"默认只生成一份匹配用户需求的报告，只有明确要求不同交付物才增加，最多 {policy.reports} 份。"
            "普通名词解释不要扩展为舆情、风险或资政研究。保留用户指定的对象、时段与约束。")
