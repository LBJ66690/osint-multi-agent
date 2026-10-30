# mcp_local_server.py -- 自建 MCP（stdio 传输）
# 提供：报告模板、本地/内部情报知识；分析人员可把经验知识录入 LOCAL_KNOWLEDGE。
# 注：沙箱内因无法通过管道 stdio 启动子进程，agents.py 改用进程内 FunctionTool；
#     本文件保留，用于正式部署（非沙箱）时以标准 MCP stdio 服务对外提供。
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from local_knowledge import LOCAL_KNOWLEDGE, REPORT_TEMPLATES, get_local_briefing, get_report_template  # noqa: E402

mcp = FastMCP("osint-local")


@mcp.tool()
def _get_report_template(report_type: str) -> str:
    """获取指定类型报告的写作模板。"""
    return get_report_template(report_type)


@mcp.tool()
def _get_local_briefing(topic: str) -> str:
    """查询本地/内部情报知识（由分析人员事先录入）。"""
    return get_local_briefing(topic)


@mcp.resource("osint://report-types")
def report_types_resource() -> str:
    """当前支持的报告类型。"""
    return "支持报告类型：" + "、".join(REPORT_TEMPLATES.keys())


@mcp.prompt()
def report_outline(report_type: str, topic: str) -> str:
    """生成报告大纲的提示模板。"""
    return f"请依据【{report_type}】模板，为主题“{topic}”拟定详细大纲。"


if __name__ == "__main__":
    mcp.run(transport="stdio")
