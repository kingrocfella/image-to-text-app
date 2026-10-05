"""Write uploaded mobile diagnostic logs into the server's own log files.

The mobile logger already redacts before upload, but the payload is untrusted
and the device may run an old or modified build, so everything is cleaned
again here: control characters are stripped (no forged log lines), sensitive
keys are redacted, and nesting and size are bounded. The logger's formatter
then applies its usual email/token redaction on top.
"""

import json
import logging
import re
from typing import Any

from app.schemas.client_log_schemas import ClientLogRecord
from app.utils.logger import logger

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|password|secret|token|email|query|filename|content|body|text|uri)",
    re.IGNORECASE,
)
_MAX_DEPTH = 4
_MAX_ITEMS = 25
_MAX_STRING = 500


def _clean_text(value: str, limit: int = _MAX_STRING) -> str:
    cleaned = _CONTROL_RE.sub(" ", value).strip()
    return cleaned if len(cleaned) <= limit else f"{cleaned[:limit]}…"


def sanitize_context(value: Any, depth: int = 0) -> Any:
    """Return a bounded, redacted copy of an untrusted JSON value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value)
    if depth >= _MAX_DEPTH:
        return "[Truncated]"
    if isinstance(value, list):
        return [sanitize_context(item, depth + 1) for item in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            safe_key = _clean_text(str(key), 80)
            cleaned[safe_key] = (
                "[REDACTED]"
                if _SENSITIVE_KEY_RE.search(safe_key)
                else sanitize_context(item, depth + 1)
            )
        return cleaned
    return _clean_text(str(value))


def ingest_client_logs(records: list[ClientLogRecord], user_id: object) -> int:
    """Log each record at its own level, tagged as mobile. Returns the count."""
    for record in records:
        context = sanitize_context(record.context) if record.context else None
        logger.log(
            _LEVELS[record.level],
            "mobile.%s.%s %s user=%s runtime=%s at=%s%s",
            record.scope,
            record.event,
            _clean_text(record.message, 1000),
            user_id,
            record.runtime_id,
            record.timestamp.isoformat(),
            f" context={json.dumps(context, separators=(',', ':'))}" if context else "",
        )
    return len(records)
