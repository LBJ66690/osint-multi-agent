# config.py -- 集中管理配置、依赖路径与模型服务
from __future__ import annotations

import os
import re
import sys
import hashlib
import json
import time
from pathlib import Path
from bailian_models import (
    BAILIAN_MODEL_IDS, DEFAULT_BAILIAN_MODEL, available_model, free_only_confirmed as bailian_free_only_confirmed,
    quota_unavailable,
)

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
RADEON_API_KEY: str = os.environ.pop("RADEON_API_KEY", "").strip()
RADEON_BASE_URL = "https://developer.amd.com.cn/radeon/api/v1"
RADEON_MODEL = "DeepSeek-V4-Flash"
EXPERIMENT_MODE = False


def chat_api_key() -> str:
    return RADEON_API_KEY if EXPERIMENT_MODE else DASHSCOPE_API_KEY


def model_provider() -> str:
    return "amd-radeon" if EXPERIMENT_MODE else "bailian"


def clear_api_keys() -> None:
    global DASHSCOPE_API_KEY, RADEON_API_KEY
    DASHSCOPE_API_KEY = RADEON_API_KEY = ""


def redact_secret(text: str) -> str:
    """阻止当前运行密钥进入日志、报告和检查点。"""
    for key in (DASHSCOPE_API_KEY, RADEON_API_KEY):
        if key:
            text = text.replace(key, "[REDACTED]")
    return text


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
    global DASHSCOPE_API_KEY, RADEON_API_KEY
    clear_api_keys()
    os.environ.pop("DASHSCOPE_API_KEY", None)
    os.environ.pop("RADEON_API_KEY", None)
    provider = "AMD Radeon Cloud" if EXPERIMENT_MODE else "Bailian"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = ""
            while not key:
                key = getpass.getpass(f"Enter {provider} API Key (hidden): ").strip()
                if not key:
                    print("API Key cannot be empty. Please try again.")
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise SystemExit("API Key input cancelled. Run in an interactive terminal.") from None
    if EXPERIMENT_MODE:
        RADEON_API_KEY = key
    else:
        DASHSCOPE_API_KEY = key

# 默认模型（可在 UI 或 .env 中覆盖）
_configured_model = os.getenv("MODEL", "").strip()
DEFAULT_MODEL: str = _configured_model if _configured_model in BAILIAN_MODEL_IDS else DEFAULT_BAILIAN_MODEL

# 业务空间 ID（可选）
WORKSPACE_ID: str = os.getenv("WORKSPACE_ID", "").strip()

# The workspace endpoint from the Bailian CSV is optional for Beijing keys.
BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "").strip().rstrip("/") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
if not re.fullmatch(r"https://(?:dashscope\.aliyuncs\.com|[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com)/compatible-mode/v1", BASE_URL):
    raise ValueError("DASHSCOPE_BASE_URL 需要使用百炼北京地域的 HTTPS 兼容端点。")

# 百炼官方 MCP：联网搜索（Streamable HTTP）。需先在百炼 MCP 广场开通 WebSearch
WEBSEARCH_MCP_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"

# 输出目录（按阶段分子目录，避免所有产物堆在一个文件夹）
def _set_output_dir(root: Path) -> None:
    global OUTPUT_DIR, REPORT_DIR, PLAN_DIR, EVIDENCE_DIR, ANALYSIS_DIR, CHECKPOINT_DIR, EVENTS_DIR, LOG_DIR
    OUTPUT_DIR = root
    REPORT_DIR = root / "reports"
    PLAN_DIR = root / "plans"
    EVIDENCE_DIR = root / "evidence"
    ANALYSIS_DIR = root / "analysis"
    CHECKPOINT_DIR = root / "checkpoints"
    EVENTS_DIR = root / "events"
    LOG_DIR = root / "logs"
    for directory in (REPORT_DIR, PLAN_DIR, EVIDENCE_DIR, ANALYSIS_DIR, CHECKPOINT_DIR, EVENTS_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


_set_output_dir(ROOT / "outputs")

import logging
from logging.handlers import RotatingFileHandler

class SecretFormatter(logging.Formatter):
    def format(self, record):
        return redact_secret(super().format(record))


def _file_handler() -> RotatingFileHandler:
    handler = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True)
    handler.setFormatter(SecretFormatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    handler.setLevel(logging.INFO)
    return handler


def _init_logger() -> logging.Logger:
    """初始化全局日志器：同时向控制台与轮转文件输出"""
    log = logging.getLogger("osint")
    if not log.handlers:
        log.setLevel(logging.INFO)
        fh = _file_handler()
        log.addHandler(fh)

        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fh.formatter)
        ch.setLevel(logging.INFO)
        log.addHandler(ch)
    return log

logger = _init_logger()

# 服务配置
HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "8765"))


def enable_experiment() -> None:
    """仅由启动入口调用，在导入服务和读取任务历史前固定实验环境。"""
    global EXPERIMENT_MODE, PORT, DASHSCOPE_API_KEY
    if EXPERIMENT_MODE:
        return
    port = int(os.getenv("RADEON_EXPERIMENT_PORT", "8766"))
    previous_log = (LOG_DIR / "app.log").resolve()
    _set_output_dir(ROOT / "outputs" / "experiments" / "radeon")
    for handler in logger.handlers[:]:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == previous_log:
            logger.removeHandler(handler)
            handler.close()
    logger.addHandler(_file_handler())
    DASHSCOPE_API_KEY = ""
    EXPERIMENT_MODE = True
    PORT = port

# 收集阶段：搜索限流与结果截断（在保证来源/要点的前提下控制 token）
SEARCH_MAX_CALLS = int(os.getenv("SEARCH_MAX_CALLS", "18"))       # 单次任务搜索硬上限
SEARCH_RESULT_MAX_CHARS = int(os.getenv("SEARCH_RESULT_MAX_CHARS", "1600"))  # 每条搜索结果回填长度

# 上下文预算兜底：当模型清单未给出具体窗口/输出上限时使用（供 harness 压缩与输出上限）
CONTEXT_WINDOW_TOKENS = int(os.getenv("CONTEXT_WINDOW_TOKENS", "131072"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))


def bailian_unavailable(model_id):
    if reason := quota_unavailable(model_id):
        return reason
    try:
        probe = json.loads((OUTPUT_DIR / "acceptance_models.json").read_text(encoding="utf-8"))
        if 0 <= time.time() - probe["tested_at"] <= 3600:
            fingerprint = hashlib.sha256(DASHSCOPE_API_KEY.encode()).hexdigest()
            if probe["account_fingerprint"] == fingerprint:
                row = next((item for item in probe["models"] if item["id"] == model_id), {})
                error = row.get("error", "").lower()
                if row.get("ok") is False and "403" in error and "quota" in error:
                    return "当前账户的近期额度检查返回 403，请选择仍可用的模型。"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return None


def preferred_bailian_model() -> str | None:
    return available_model(DEFAULT_MODEL, bailian_unavailable)


def resolve_model(model_id: str | None = None) -> str:
    """显式选择失效时提示用户，自动选择仅使用名单中仍启用的模型。"""
    if EXPERIMENT_MODE:
        return RADEON_MODEL
    selected = (model_id or "").strip() or preferred_bailian_model()
    if not selected:
        raise ValueError("当前没有可用的免费额度模型。")
    if reason := bailian_unavailable(selected):
        raise ValueError(f"{selected}：{reason}")
    return selected


def check_key() -> None:
    """启动前校验：没有 Key 就给出明确提示。"""
    if not chat_api_key():
        request_api_key()
    if EXPERIMENT_MODE:
        if not RADEON_API_KEY:
            raise SystemExit("请运行 python launch.py --experiment 并输入 AMD Radeon Cloud API Key。")
        return
    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY.startswith("sk-在这里"):
        raise SystemExit(
            "\n[配置错误] 未检测到 DASHSCOPE_API_KEY。\n"
            "请运行 启动项目.bat，或 python launch.py，按提示输入百炼 API Key。\n"
            "获取地址：https://help.aliyun.com/zh/model-studio/get-api-key\n"
        )
