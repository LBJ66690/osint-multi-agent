"""Bailian model IDs and expiry dates shown in the supplied free-quota snapshot."""

from datetime import date, datetime, timedelta, timezone
import os

DEFAULT_BAILIAN_MODEL = "qwen3.8-flash"
QUOTA_EXPIRY = {
    "qwen4-max": date(2027, 2, 14),
    "qwen3.8-flash": date(2026, 11, 25),
    "kimi-k3.1-pro": date(2027, 2, 2),
    "deepseek-v4.1-flash-1017": date(2027, 1, 17),
    "deepseek-v4.1-flash": date(2026, 12, 13),
    "glm-5.3": date(2026, 11, 23),
}
BAILIAN_MODEL_IDS = tuple(QUOTA_EXPIRY)
FALLBACK_ORDER = (
    DEFAULT_BAILIAN_MODEL, "deepseek-v4.1-flash-1017", "qwen4-max", "kimi-k3.1-pro",
    "deepseek-v4.1-flash", "glm-5.3",
)
CHINA = timezone(timedelta(hours=8))
_exhausted: set[str] = set()


def require_bailian_model(model_id: str) -> str:
    if model_id not in BAILIAN_MODEL_IDS:
        raise ValueError(f"未知或未启用的百炼模型：{model_id}")
    return model_id


def quota_unavailable(model_id: str, today: date | None = None) -> str | None:
    if model_id not in QUOTA_EXPIRY:
        return "模型不在当前账号的额度名单中。"
    if (today or datetime.now(CHINA).date()) >= QUOTA_EXPIRY[model_id]:
        return f"截图登记的免费额度已到停用日 {QUOTA_EXPIRY[model_id].isoformat()}。"
    if model_id in _exhausted:
        return "百炼已报告该模型的免费额度不可用。"
    if model_id in {item.strip() for item in os.getenv("OSINT_DISABLED_MODELS", "").split(",")}:
        return "管理员已暂停调用该模型。"
    return None


def available_model(preferred=DEFAULT_BAILIAN_MODEL, unavailable=quota_unavailable) -> str | None:
    return next((model for model in dict.fromkeys((preferred, *FALLBACK_ORDER)) if not unavailable(model)), None)


def mark_exhausted(model_id: str):
    if model_id in QUOTA_EXPIRY:
        _exhausted.add(model_id)


def free_only_confirmed():
    return os.getenv("OSINT_BAILIAN_FREE_ONLY_CONFIRMED", "").strip() == "1"
