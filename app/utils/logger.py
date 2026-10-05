"""Logging configuration for the application.

Stdout always. With ``LOG_TO_FILE=true`` the process also writes two rotating
files into ``LOG_DIR``: ``info.log`` (everything) and ``errors.log`` (errors
only). In Docker ``LOG_DIR`` is the named ``app_logs`` volume, so both survive
rebuilds. Never write logs elsewhere, and never collapse the two files.

Redaction happens in the formatter, at the final sink, so exception text and
tracebacks cannot bypass call-site discipline.
"""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import get_settings

_settings = get_settings()
LOG_LEVEL = _settings.log_level

_project_root = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger("app")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
# Prevent propagation to root logger to avoid duplicate logs
logger.propagate = False

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_TOKEN_PARAM_RE = re.compile(r"(?i)(token=)[^\s&]+")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/tmp|/var/folders|/app/shared_files)/[^\s:'\"]+", re.I
)


def sanitize_log_text(value: str) -> str:
    """Redact account identifiers and credentials from rendered log output."""
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = _JWT_RE.sub("[REDACTED_TOKEN]", value)
    value = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", value)
    value = _TOKEN_PARAM_RE.sub(r"\1[REDACTED_TOKEN]", value)
    return _PRIVATE_PATH_RE.sub("[REDACTED_PATH]", value)


class SanitizingFormatter(logging.Formatter):
    """Sanitize the final record, including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_log_text(super().format(record))


# Prevent duplicate logs
if logger.handlers:
    logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
console_handler.setFormatter(
    SanitizingFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logger.addHandler(console_handler)

if _settings.log_to_file:
    file_format = SanitizingFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_dir = (
        Path(_settings.log_dir)
        if os.path.isabs(_settings.log_dir)
        else (_project_root / _settings.log_dir).resolve()
    )
    max_bytes = _settings.log_max_size_mb * 1024 * 1024
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(log_dir / "info.log"),
            maxBytes=max_bytes,
            backupCount=_settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

        error_file_handler = RotatingFileHandler(
            str(log_dir / "errors.log"),
            maxBytes=max_bytes,
            backupCount=_settings.log_backup_count,
            encoding="utf-8",
        )
        error_file_handler.setLevel(logging.ERROR)
        error_file_handler.setFormatter(file_format)
        logger.addHandler(error_file_handler)
    except OSError as exc:
        # Don't crash the app over logging setup; fall back to stdout only.
        print(
            f"Warning: file logging requested but unavailable ({exc}); "
            "continuing with stdout only.",
            file=sys.stderr,
        )

# Set levels for third-party loggers
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
