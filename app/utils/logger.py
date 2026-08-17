"""Logging configuration for the application."""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Get log level from environment or default to INFO
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR_STR = os.getenv("LOG_DIR", "logs")

# Determine the base directory for logs
# Use the project root (parent of app directory) as the base, or current working directory as fallback
_this_file = Path(__file__).resolve()
_app_dir = _this_file.parent.parent
_project_root = _app_dir.parent

# Resolve to absolute path to ensure consistent location
# If absolute path provided, use it; otherwise make it relative to project root
if os.path.isabs(LOG_DIR_STR):
    LOG_DIR = Path(LOG_DIR_STR)
else:
    LOG_DIR = (_project_root / LOG_DIR_STR).resolve()

# Create logs directory if it doesn't exist (create parents if needed)
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = LOG_DIR / "app.log"
    ERROR_LOG_FILE = LOG_DIR / "errors.log"
except OSError as e:
    # If directory creation fails, log to stderr and continue with console logging only
    print(f"Warning: Could not create log directory {LOG_DIR}: {e}", file=sys.stderr)
    LOG_DIR = None
    LOG_FILE = None
    ERROR_LOG_FILE = None

# Configure root logger
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

# Console handler with colored output
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
console_format = SanitizingFormatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
console_handler.setFormatter(console_format)
logger.addHandler(console_handler)

# File handler for all logs (only if LOG_DIR was created successfully)
if LOG_DIR and LOG_FILE:
    try:
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_format = SanitizingFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
        # Write initial log to create the file (use INFO level so it's always written)
        logger.info("Logging initialized - log file: %s", LOG_FILE)
    except (OSError, IOError) as e:
        print(f"Warning: Could not create log file {LOG_FILE}: {e}", file=sys.stderr)

# Error file handler for errors only (only if LOG_DIR was created successfully)
if LOG_DIR and ERROR_LOG_FILE:
    try:
        error_file_handler = RotatingFileHandler(
            str(ERROR_LOG_FILE),  # Convert Path to string for compatibility
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        error_file_handler.setLevel(logging.ERROR)
        error_file_handler.setFormatter(file_format)
        logger.addHandler(error_file_handler)
    except (OSError, IOError) as e:
        print(
            f"Warning: Could not create error log file {ERROR_LOG_FILE}: {e}",
            file=sys.stderr,
        )

# Set levels for third-party loggers
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
