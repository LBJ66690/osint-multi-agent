# local_knowledge.py -- 本地情报知识与报告模板（纯数据 + 查询函数）
# 供两种方式共用：
#   1) mcp_local_server.py  —— 以独立 stdio MCP 服务暴露（正式部署时用）；
#   2) agents.py             —— 以进程内 FunctionTool 直接注入智能体（沙箱内无子进程时用）。

REPORT_TEMPLATES = {
    "简明解答": "先直接回答问题，再给出依据、必要的歧义与不确定性，最后列出引用来源。",
    "研究报告": "研究问题与范围；主要结论；证据与不同意见；资料局限；引用来源。",
    "舆情分析": (
        "《{topic} 舆情分析报告》\n"
        "一、舆情概况（总体声量与情感分布）\n"
        "二、主要正面评价（含具体例子与来源）\n"
        "三、主要负面评价与槽点（含具体例子与来源）\n"
        "四、热点话题与典型案例\n"
        "五、研判结论与应对建议"
    ),
    "资政建议": (
        "《关于 {topic} 的资政建议》\n"
        "一、现状与基本判断\n"
        "二、存在的问题（附证据与反证）\n"
        "三、对策建议（分条，要求可落地）\n"
        "四、预期成效与风险提示\n"
        "五、附件：证据来源清单"
    ),
}

# 本地/内部情报知识：分析人员可在此录入自己的经验或内部资料
LOCAL_KNOWLEDGE = {
    "贵阳花溪区": (
        "花溪区位于贵阳市南部，拥有花溪公园、十里河滩湿地公园、青岩古镇、"
        "天河潭等景区，是贵阳重要的文旅板块；高校聚集（贵州大学等），"
        "兼具生态、人文与科教资源。"
    ),
}


def get_report_template(report_type: str) -> str:
    """获取指定类型报告的写作模板。"""
    t = REPORT_TEMPLATES.get(report_type.strip())
    if t:
        return t
    return f"【{report_type}】\n" + REPORT_TEMPLATES["研究报告"]


def get_local_briefing(topic: str) -> str:
    """查询本地/内部情报知识（由分析人员事先录入）。"""
    topic = topic.strip()
    for k, v in LOCAL_KNOWLEDGE.items():
        if k in topic or topic in k:
            return v
    return f"本地知识库暂无【{topic}】条目。"
