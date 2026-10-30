# schemas.py -- 各智能体之间传递的结构化数据（Pydantic schema）
# 对应书第 4 章：用 schema 约束模型输出，便于校验与串联。
from pydantic import BaseModel, Field
from typing import Literal


# ① 规划官输出
class SubQuestion(BaseModel):
    question: str = Field(description="拆解出的子问题")
    search_queries: list[str] = Field(
        default_factory=list,
        description="为该子问题准备的搜索关键词，1~3 条",
    )


class ResearchPlan(BaseModel):
    goal: str = Field(description="用一句话复述用户的真实目标")
    subquestions: list[SubQuestion] = Field(description="子问题列表")
    report_types: list[str] = Field(
        description="建议生成的报告类型，例如：舆情分析、资政建议"
    )


# ② 收集官输出
class EvidenceItem(BaseModel):
    subquestion: str = Field(description="该证据对应哪个子问题")
    title: str = Field(description="资料标题或摘要标题")
    content: str = Field(description="资料正文要点")
    source: str = Field(description="来源 URL 或平台名称")
    provider: str = Field(default="", description="搜索渠道 ID")
    retrieved_at: str = Field(default="", description="采集时间 ISO 8601")


# ③ 分析官输出
class Mention(BaseModel):
    entity: str = Field(description="原文中出现的实体名")
    canonical: str = Field(description="归一化后的标准名")
    type: str = Field(description="实体类型：景区/地点/人物/话题/其他")


class ViewPoint(BaseModel):
    topic: str = Field(description="观点所围绕的对象或话题")
    sentiment: str = Field(description="情感倾向：正面/中性/负面")
    summary: str = Field(description="观点的归纳总结")
    examples: list[str] = Field(default_factory=list, description="支撑该观点的具体例子")


class Knowledge(BaseModel):
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="别名/同义词 -> 标准名，例如：黔 -> 贵州",
    )
    entities: list[Mention] = Field(default_factory=list)
    viewpoints: list[ViewPoint] = Field(default_factory=list)


# ④ 推理官输出
class Finding(BaseModel):
    claim: str = Field(description="一条核心结论")
    evidence: list[str] = Field(default_factory=list, description="支持该结论的证据")
    counter_evidence: list[str] = Field(default_factory=list, description="反证或不同声音")
    sources: list[str] = Field(default_factory=list, description="来源")


class Analysis(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class QuickSynthesis(BaseModel):
    knowledge: Knowledge
    analysis: Analysis


# ⑤ 核查官输出（各阶段质量关卡 + 报告事实核查）
class VerificationIssue(BaseModel):
    claim: str = Field(description="被核查的具体陈述")
    verdict: str = Field(description="核查结论：属实 / 存疑 / 无出处 / 推测当事实")
    explanation: str = Field(description="核查依据：对照了哪些证据或发现，或缺少什么支撑")
    severity: str = Field(description="严重程度：高 / 中 / 低")


class VerificationReport(BaseModel):
    verdict: Literal['pass','fail'] = Field(description="关卡判定：pass（通过，放行）/ fail（未通过，需打回重做）")
    summary: str = Field(description="总体核查结论，一两句话")
    issues: list[VerificationIssue] = Field(
        default_factory=list, description="问题清单；核查通过则为空列表"
    )
    redo_targets: list[str] = Field(
        default_factory=list,
        description="判 fail 时需要重做的目标：收集阶段=证据不足的子问题原文；报告阶段=需重写的报告类型；其他阶段为空",
    )
