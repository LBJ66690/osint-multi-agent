# config.py -- 集中管理配置、依赖路径与百炼端点
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---- 依赖引导：vendor/ 仅作离线兜底（置于路径末尾），主环境已装的正确版本优先，
#      避免 vendor 内旧编译扩展（如 cp310 的 msgspec）在新版 Python 下被误用 ----
VENDOR = ROOT / "vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.append(str(VENDOR))

from dotenv import dotenv_values  # noqa: E402

# .env 只加载非密钥设置；密钥只能来自本次进程或交互输入。
for _name, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None and not any(word in _name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
        os.environ.setdefault(_name, _value)

# 百炼 API Key
DASHSCOPE_API_KEY: str = os.environ.pop("DASHSCOPE_API_KEY", "").strip()


def redact_secret(text: str) -> str:
    """阻止当前运行密钥进入日志、报告和检查点。"""
    return text.replace(DASHSCOPE_API_KEY, "[REDACTED]") if DASHSCOPE_API_KEY else text


def redact_data(value):
    if isinstance(value, str):
        return redact_secret(value)
    if isinstance(value, dict):
        return {redact_secret(k) if isinstance(k, str) else k: redact_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_data(v) for v in value]
    return value


def request_api_key() -> None:
    """强制隐藏输入；不回显、不写文件、不传递给浏览器子进程。"""
    import getpass
    import warnings
    global DASHSCOPE_API_KEY
    DASHSCOPE_API_KEY = ""
    os.environ.pop("DASHSCOPE_API_KEY", None)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            while not DASHSCOPE_API_KEY:
                DASHSCOPE_API_KEY = getpass.getpass("Enter Bailian API Key (hidden): ").strip()
                if not DASHSCOPE_API_KEY:
                    print("API Key cannot be empty. Please try again.")
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise SystemExit("API Key input cancelled. Run in an interactive terminal.") from None

# 默认模型（可在 UI 或 .env 中覆盖）
DEFAULT_MODEL: str = os.getenv("MODEL", "qwen3.8-max").strip()

# 业务空间 ID（可选）
WORKSPACE_ID: str = os.getenv("WORKSPACE_ID", "").strip()

# 百炼 OpenAI 兼容端点（无需业务空间）
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 百炼官方 MCP：联网搜索（Streamable HTTP）。需先在百炼 MCP 广场开通 WebSearch
WEBSEARCH_MCP_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"

# 输出目录（按阶段分子目录，避免所有产物堆在一个文件夹）
OUTPUT_DIR = ROOT / "outputs"
REPORT_DIR = OUTPUT_DIR / "reports"      # 报告官：最终 .md 报告
PLAN_DIR = OUTPUT_DIR / "plans"          # 规划官：研究计划 JSON
EVIDENCE_DIR = OUTPUT_DIR / "evidence"   # 收集官：证据资料 JSON
ANALYSIS_DIR = OUTPUT_DIR / "analysis"   # 分析/推理官：知识与发现 JSON
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints" # 检查点状态暂存
EVENTS_DIR = OUTPUT_DIR / "events"        # 运行过程事件流（历史回放持久化）
LOG_DIR = OUTPUT_DIR / "logs"            # 系统与排错日志
for _d in (OUTPUT_DIR, REPORT_DIR, PLAN_DIR, EVIDENCE_DIR, ANALYSIS_DIR, CHECKPOINT_DIR, EVENTS_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

import logging
from logging.handlers import RotatingFileHandler

class SecretFormatter(logging.Formatter):
    def format(self, record):
        return redact_secret(super().format(record))

def _init_logger() -> logging.Logger:
    """初始化全局日志器：同时向控制台与轮转文件输出"""
    log = logging.getLogger("osint")
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = SecretFormatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        fh = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.INFO)
        log.addHandler(fh)

        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.setLevel(logging.INFO)
        log.addHandler(ch)
    return log

logger = _init_logger()

# 服务配置
HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "8765"))

# 收集阶段：搜索限流与结果截断（在保证来源/要点的前提下控制 token）
SEARCH_MAX_CALLS = int(os.getenv("SEARCH_MAX_CALLS", "18"))       # 单次任务搜索硬上限
SEARCH_RESULT_MAX_CHARS = int(os.getenv("SEARCH_RESULT_MAX_CHARS", "1600"))  # 每条搜索结果回填长度

# 上下文预算兜底：当模型清单未给出具体窗口/输出上限时使用（供 harness 压缩与输出上限）
CONTEXT_WINDOW_TOKENS = int(os.getenv("CONTEXT_WINDOW_TOKENS", "131072"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))


def resolve_model(model_id: str | None = None) -> str:
    """解析最终使用的模型 id。未指定/为空时回退到默认。"""
    model_id = (model_id or "").strip() or DEFAULT_MODEL
    return model_id


def check_key() -> None:
    """启动前校验：没有 Key 就给出明确提示。"""
    if not DASHSCOPE_API_KEY:
        request_api_key()
    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY.startswith("sk-在这里"):
        raise SystemExit(
            "\n[配置错误] 未检测到 DASHSCOPE_API_KEY。\n"
            "请运行 启动项目.bat，或 python launch.py，按提示输入百炼 API Key。\n"
            "获取地址：https://help.aliyun.com/zh/model-studio/get-api-key\n"
        )
