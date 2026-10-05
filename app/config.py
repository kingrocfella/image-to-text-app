"""The one place the server reads its environment.

Every setting the API, the worker, and the tooling use is declared, parsed,
and validated here, once, at startup. Nothing else in ``app/`` calls
``os.getenv`` — ``scripts/check-env.sh`` enforces that, and it also derives the
list of keys ``.env`` must contain from the quoted names in this file.

Configuration fails closed: a malformed value, or a value production cannot
run safely with, stops the process at import time with every problem listed,
rather than surfacing later as a confusing runtime error. An empty value means
"use the default" for optional settings.

``.env`` beside ``app/`` is the only environment file in this repository.
Real process variables win over it, so Compose and deployments override values
without editing the file.
"""

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
# Lowercase so scripts/check-env.sh does not mistake them for env keys.
_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ADMIN_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "your-")
# Lowercase so scripts/check-env.sh does not mistake them for env keys.
_CLOUD_MODEL_NAMES = {"openai", "gemini", "deepseek", "claude"}


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a valid configuration."""


@dataclass(frozen=True)
class Settings:
    # Runtime
    environment: str
    app_url: str
    cors_allowed_origins: tuple[str, ...]
    minimum_app_version: str
    max_request_body_bytes: int
    request_timeout_seconds: float
    trust_proxy_headers: bool

    # Logging
    log_level: str
    log_to_file: bool
    log_dir: str
    log_max_size_mb: int
    log_backup_count: int

    # Datastores
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    postgres_port: int
    redis_host: str
    redis_port: int
    redis_db: int
    qdrant_url: str

    # Auth
    secret_key: str
    secret_key_previous: str
    jwt_issuer: str
    jwt_audience: str
    access_token_expire_hours: int
    refresh_token_expire_days: int
    verification_token_expire_hours: int
    password_reset_token_expire_minutes: int
    security_purge_interval_seconds: int

    # Outbound email
    smtp_server: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_name: str

    # Upload bounds
    image_max_bytes: int
    image_max_pixels: int
    image_max_frames: int
    audio_max_bytes: int
    pdf_max_bytes: int
    pdf_max_pages: int

    # Retention
    rag_retention_days: int
    job_type_ttl_days: int

    # Models
    ollama_url: str
    ollama_model: str
    ollama_temperature: float
    ollama_num_predict: int
    ollama_timeout_seconds: float
    openai_api_key: str
    gemini_api_key: str
    deepseek_api_key: str
    anthropic_api_key: str
    # Which model each provider answers with. Chosen for cost per answer
    # (docs/models.md); change one here, never in code.
    openai_model: str
    gemini_model: str
    deepseek_model: str
    claude_model: str
    embedding_model: str
    # Chunks of the PDF sent with each question, and the cap on an answer:
    # together they bound what one question can cost.
    rag_top_k: int
    cloud_model_max_output_tokens: int
    # Cloud models a free account may use (within its allowance). The rest
    # need ScanGenAI Pro.
    free_cloud_models: tuple[str, ...]

    # Per-user monthly allowances (docs/billing.md): free, then Pro
    quota_image_monthly: int
    quota_sound_monthly: int
    quota_pdf_monthly: int
    quota_cloud_model_monthly: int
    pro_quota_image_monthly: int
    pro_quota_sound_monthly: int
    pro_quota_pdf_monthly: int
    pro_quota_cloud_model_monthly: int

    # ScanGenAI Pro subscriptions (docs/billing.md)
    billing_provider: str
    apple_environment: str
    apple_app_id: int
    apple_root_ca_path: str
    google_play_service_account_json: str

    # Social sign-in and native app identity
    google_web_client_id: str
    google_oauth_timeout_s: float
    android_package_name: str
    ios_bundle_id: str

    # Operations: admin console, operator email, monitoring
    admin_dashboard_username: str
    admin_dashboard_token: str
    admin_dashboard_path: str
    admin_auto_logout_minutes: int
    admin_lock_threshold: int
    admin_lock_minutes: int
    notify_emails_enabled: bool
    notify_email_to: str
    backup_max_age_hours: int
    ops_check_interval_minutes: int
    ops_alert_repeat_hours: int

    @property
    def is_production(self) -> bool:
        return self.environment != "dev"

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_dashboard_token)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_server and self.smtp_username and self.smtp_password)

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


class _Reader:
    """Parses raw values, collecting every problem instead of stopping at one."""

    def __init__(self, environ: dict[str, str]) -> None:
        self._environ = environ
        self.errors: list[str] = []

    def _raw(self, key: str) -> str:
        return self._environ.get(key, "").strip()

    def text(self, key: str, default: str = "") -> str:
        return self._raw(key) or default

    def required(self, key: str) -> str:
        value = self._raw(key)
        if not value:
            self.errors.append(f"{key} is required")
        return value

    def flag(self, key: str, default: bool) -> bool:
        raw = self._raw(key).lower()
        if not raw:
            return default
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
        self.errors.append(f"{key} must be true or false")
        return default

    def integer(
        self,
        key: str,
        default: int,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw = self._raw(key)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors.append(f"{key} must be an integer")
            return default
        if minimum is not None and value < minimum:
            self.errors.append(f"{key} must be at least {minimum}")
        if maximum is not None and value > maximum:
            self.errors.append(f"{key} must be at most {maximum}")
        return value

    def number(
        self,
        key: str,
        default: float,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        raw = self._raw(key)
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            self.errors.append(f"{key} must be a number")
            return default
        if minimum is not None and value < minimum:
            self.errors.append(f"{key} must be at least {minimum}")
        if maximum is not None and value > maximum:
            self.errors.append(f"{key} must be at most {maximum}")
        return value

    def csv(self, key: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in self._raw(key).split(",") if item.strip())


def _positive(reader: _Reader, key: str, default: int) -> int:
    return reader.integer(key, default, minimum=1)


def _quota(reader: _Reader, key: str, default: int) -> int:
    """A monthly allowance. 0 turns the feature off for everyone."""
    return reader.integer(key, default, minimum=0)


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Build and validate settings from ``environ`` (the process env by default)."""
    if environ is None:
        load_dotenv(ENV_PATH, override=False)
        environ = dict(os.environ)
    r = _Reader(environ)

    settings = Settings(
        environment=r.text("ENVIRONMENT", "dev"),
        app_url=r.text("APP_URL").rstrip("/"),
        cors_allowed_origins=r.csv("CORS_ALLOWED_ORIGINS"),
        minimum_app_version=r.text("MINIMUM_APP_VERSION", "0.0.0"),
        max_request_body_bytes=_positive(r, "MAX_REQUEST_BODY_BYTES", 25 * 1024 * 1024),
        request_timeout_seconds=r.number("REQUEST_TIMEOUT_SECONDS", 30.0, minimum=1),
        trust_proxy_headers=r.flag("TRUST_PROXY_HEADERS", False),
        log_level=r.text("LOG_LEVEL", "info").upper(),
        log_to_file=r.flag("LOG_TO_FILE", False),
        log_dir=r.text("LOG_DIR", "logs"),
        log_max_size_mb=_positive(r, "LOG_MAX_SIZE_MB", 10),
        log_backup_count=_positive(r, "LOG_BACKUP_COUNT", 5),
        postgres_user=r.required("POSTGRES_USER"),
        postgres_password=r.required("POSTGRES_PASSWORD"),
        postgres_db=r.required("POSTGRES_DB"),
        postgres_host=r.required("POSTGRES_HOST"),
        postgres_port=_positive(r, "POSTGRES_PORT", 5432),
        redis_host=r.text("REDIS_HOST", "redis"),
        redis_port=_positive(r, "REDIS_PORT", 6379),
        redis_db=r.integer("REDIS_DB", 0, minimum=0),
        qdrant_url=r.text("QDRANT_URL", "http://qdrant:6333").rstrip("/"),
        secret_key=r.required("SECRET_KEY"),
        secret_key_previous=r.text("SECRET_KEY_PREVIOUS"),
        jwt_issuer=r.text("JWT_ISSUER", "scangenai-api"),
        jwt_audience=r.text("JWT_AUDIENCE", "scangenai-client"),
        access_token_expire_hours=_positive(r, "ACCESS_TOKEN_EXPIRE_HOURS", 1),
        refresh_token_expire_days=_positive(r, "REFRESH_TOKEN_EXPIRE_DAYS", 30),
        verification_token_expire_hours=_positive(
            r, "VERIFICATION_TOKEN_EXPIRE_HOURS", 24
        ),
        password_reset_token_expire_minutes=_positive(
            r, "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", 60
        ),
        security_purge_interval_seconds=_positive(
            r, "SECURITY_PURGE_INTERVAL_SECONDS", 3600
        ),
        smtp_server=r.text("SMTP_SERVER"),
        smtp_port=_positive(r, "SMTP_PORT", 587),
        smtp_username=r.text("SMTP_USERNAME"),
        smtp_password=r.text("SMTP_PASSWORD"),
        smtp_from_name=r.text("SMTP_FROM_NAME", "Leon Frontier ScanGenAI"),
        image_max_bytes=_positive(r, "IMAGE_MAX_BYTES", 10 * 1024 * 1024),
        image_max_pixels=_positive(r, "IMAGE_MAX_PIXELS", 40_000_000),
        image_max_frames=_positive(r, "IMAGE_MAX_FRAMES", 20),
        audio_max_bytes=_positive(r, "AUDIO_MAX_BYTES", 20 * 1024 * 1024),
        pdf_max_bytes=_positive(r, "PDF_MAX_BYTES", 20 * 1024 * 1024),
        pdf_max_pages=_positive(r, "PDF_MAX_PAGES", 100),
        rag_retention_days=_positive(r, "RAG_RETENTION_DAYS", 30),
        job_type_ttl_days=_positive(r, "JOB_TYPE_TTL_DAYS", 7),
        ollama_url=r.text("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
        ollama_model=r.text("OLLAMA_MODEL", "llama3.2:3b"),
        ollama_temperature=r.number("OLLAMA_TEMPERATURE", 0.7, minimum=0, maximum=2),
        ollama_num_predict=_positive(r, "OLLAMA_NUM_PREDICT", 500),
        ollama_timeout_seconds=r.number("OLLAMA_TIMEOUT_SECONDS", 300.0, minimum=1),
        openai_api_key=r.text("OPENAI_API_KEY"),
        gemini_api_key=r.text("GEMINI_API_KEY"),
        deepseek_api_key=r.text("DEEPSEEK_API_KEY"),
        anthropic_api_key=r.text("ANTHROPIC_API_KEY"),
        openai_model=r.text("OPENAI_MODEL", "gpt-6-luna"),
        gemini_model=r.text("GEMINI_MODEL", "gemini-3.8-flash"),
        deepseek_model=r.text("DEEPSEEK_MODEL", "deepseek-flash"),
        claude_model=r.text("CLAUDE_MODEL", "claude-haiku-4-5"),
        embedding_model=r.text("EMBEDDING_MODEL", "text-embedding-3-small"),
        rag_top_k=r.integer("RAG_TOP_K", 8, minimum=1, maximum=50),
        cloud_model_max_output_tokens=r.integer(
            "CLOUD_MODEL_MAX_OUTPUT_TOKENS", 800, minimum=64, maximum=8000
        ),
        free_cloud_models=(
            tuple(m.lower() for m in r.csv("FREE_CLOUD_MODELS"))
            if r.text("FREE_CLOUD_MODELS")
            else ("gemini", "deepseek")
        ),
        quota_image_monthly=_quota(r, "QUOTA_IMAGE_MONTHLY", 100),
        quota_sound_monthly=_quota(r, "QUOTA_SOUND_MONTHLY", 30),
        quota_pdf_monthly=_quota(r, "QUOTA_PDF_MONTHLY", 50),
        quota_cloud_model_monthly=_quota(r, "QUOTA_CLOUD_MODEL_MONTHLY", 10),
        pro_quota_image_monthly=_quota(r, "PRO_QUOTA_IMAGE_MONTHLY", 1000),
        pro_quota_sound_monthly=_quota(r, "PRO_QUOTA_SOUND_MONTHLY", 300),
        pro_quota_pdf_monthly=_quota(r, "PRO_QUOTA_PDF_MONTHLY", 500),
        pro_quota_cloud_model_monthly=_quota(r, "PRO_QUOTA_CLOUD_MODEL_MONTHLY", 300),
        billing_provider=r.text("BILLING_PROVIDER", "off").lower(),
        apple_environment=r.text("APPLE_ENVIRONMENT", "Production"),
        apple_app_id=r.integer("APPLE_APP_ID", 0, minimum=0),
        apple_root_ca_path=r.text("APPLE_ROOT_CA_PATH", "certs/apple-root-ca-g3.pem"),
        google_play_service_account_json=r.text("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"),
        google_web_client_id=r.text("GOOGLE_WEB_CLIENT_ID"),
        google_oauth_timeout_s=r.number("GOOGLE_OAUTH_TIMEOUT_S", 5.0, minimum=1),
        android_package_name=r.text(
            "ANDROID_PACKAGE_NAME", "com.leonfrontier.scangenai"
        ),
        ios_bundle_id=r.text("IOS_BUNDLE_ID", "com.leonfrontier.scangenai"),
        admin_dashboard_username=r.text("ADMIN_DASHBOARD_USERNAME", "admin"),
        admin_dashboard_token=r.text("ADMIN_DASHBOARD_TOKEN"),
        admin_dashboard_path=r.text("ADMIN_DASHBOARD_PATH", "/admin").rstrip("/"),
        admin_auto_logout_minutes=_positive(r, "ADMIN_AUTO_LOGOUT_MINUTES", 15),
        admin_lock_threshold=_positive(r, "ADMIN_LOCK_THRESHOLD", 5),
        admin_lock_minutes=_positive(r, "ADMIN_LOCK_MINUTES", 30),
        notify_emails_enabled=r.flag("NOTIFY_EMAILS_ENABLED", False),
        notify_email_to=r.text("NOTIFY_EMAIL_TO"),
        backup_max_age_hours=_positive(r, "BACKUP_MAX_AGE_HOURS", 48),
        ops_check_interval_minutes=_positive(r, "OPS_CHECK_INTERVAL_MINUTES", 60),
        ops_alert_repeat_hours=_positive(r, "OPS_ALERT_REPEAT_HOURS", 24),
    )

    errors = r.errors + _validate(settings)
    if errors:
        raise ConfigError(
            "Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    return settings


def _validate(s: Settings) -> list[str]:
    """Cross-field rules, plus what production cannot run without."""
    errors = _validate_formats(s)
    if s.is_production:
        errors += _validate_production(s)
    return errors


def _is_placeholder(value: str) -> bool:
    return value.lower().startswith(_PLACEHOLDER_PREFIXES)


def _validate_formats(s: Settings) -> list[str]:
    errors: list[str] = []
    if s.environment not in {"dev", "production"}:
        errors.append("ENVIRONMENT must be dev or production")
    if s.secret_key and (len(s.secret_key) < 32 or _is_placeholder(s.secret_key)):
        errors.append("SECRET_KEY must be at least 32 non-placeholder characters")
    if s.secret_key_previous and len(s.secret_key_previous) < 32:
        errors.append("SECRET_KEY_PREVIOUS must be empty or at least 32 characters")
    if s.secret_key_previous and s.secret_key_previous == s.secret_key:
        errors.append("SECRET_KEY_PREVIOUS must differ from SECRET_KEY")
    if not s.jwt_issuer or not s.jwt_audience:
        errors.append("JWT_ISSUER and JWT_AUDIENCE must not be empty")
    if s.log_level.lower() not in _LOG_LEVELS:
        levels = ", ".join(sorted(level.upper() for level in _LOG_LEVELS))
        errors.append(f"LOG_LEVEL must be one of {levels}")
    if not _VERSION_RE.match(s.minimum_app_version):
        errors.append("MINIMUM_APP_VERSION must be MAJOR.MINOR.PATCH")
    if "*" in s.cors_allowed_origins:
        errors.append("CORS_ALLOWED_ORIGINS must list explicit origins, never *")
    return (
        errors
        + _app_url_errors(s.app_url)
        + _operations_errors(s)
        + _billing_errors(s)
        + _model_errors(s)
    )


def _model_errors(s: Settings) -> list[str]:
    errors: list[str] = []
    unknown = set(s.free_cloud_models) - _CLOUD_MODEL_NAMES
    if unknown:
        errors.append(
            "FREE_CLOUD_MODELS may only name openai, gemini, deepseek or claude"
        )
    if "openai" in s.free_cloud_models:
        # Owner decision, 2026-10-05: OpenAI is for subscribers only.
        errors.append("FREE_CLOUD_MODELS must not include openai (Pro only)")
    return errors


def resolve_server_path(path: str) -> Path:
    """A configured file path; relative ones are relative to the server root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ENV_PATH.parent / candidate


def _billing_errors(s: Settings) -> list[str]:
    if s.billing_provider not in {"off", "dev", "store"}:
        return ["BILLING_PROVIDER must be off, dev or store"]
    if s.apple_environment not in {"Production", "Sandbox"}:
        return ["APPLE_ENVIRONMENT must be Production or Sandbox"]
    if s.billing_provider != "store":
        return []
    errors: list[str] = []
    if s.apple_environment == "Production" and s.apple_app_id <= 0:
        errors.append("BILLING_PROVIDER=store needs APPLE_APP_ID (App Store Connect)")
    if not resolve_server_path(s.apple_root_ca_path).is_file():
        errors.append("APPLE_ROOT_CA_PATH must point at the Apple Root CA G3 PEM")
    try:
        account = json.loads(s.google_play_service_account_json or "null")
    except ValueError:
        account = None
    if (
        not isinstance(account, dict)
        or not {
            "client_email",
            "private_key",
        }
        <= account.keys()
    ):
        errors.append(
            "BILLING_PROVIDER=store needs GOOGLE_PLAY_SERVICE_ACCOUNT_JSON: the Play "
            "service account key as one-line JSON"
        )
    return errors


def _operations_errors(s: Settings) -> list[str]:
    errors: list[str] = []
    if s.admin_enabled:
        if len(s.admin_dashboard_token) < 32:
            errors.append(
                "ADMIN_DASHBOARD_TOKEN must be empty or at least 32 characters"
            )
        if not _ADMIN_PATH_RE.match(s.admin_dashboard_path):
            errors.append(
                "ADMIN_DASHBOARD_PATH must be one path segment, e.g. /ops-1a2b"
            )
        if not s.admin_dashboard_username.strip():
            errors.append("ADMIN_DASHBOARD_USERNAME is required when the console is on")
    if s.notify_emails_enabled:
        if not _EMAIL_RE.match(s.notify_email_to):
            errors.append("NOTIFY_EMAIL_TO must be an email address when enabled")
        if not s.smtp_configured:
            errors.append("NOTIFY_EMAILS_ENABLED needs SMTP_SERVER/USERNAME/PASSWORD")
    return errors


def _app_url_errors(app_url: str) -> list[str]:
    if not app_url:
        return []
    parsed = urlparse(app_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ["APP_URL must be an absolute http(s) URL"]
    if parsed.path or parsed.query or parsed.fragment:
        return ["APP_URL must be an origin with no path, query, or fragment"]
    return []


def _validate_production(s: Settings) -> list[str]:
    errors: list[str] = []
    if not s.app_url:
        errors.append("APP_URL is required in production")
    elif not s.app_url.startswith("https://"):
        errors.append("APP_URL must use https in production")
    if not s.smtp_configured or any(
        _is_placeholder(v) for v in (s.smtp_server, s.smtp_username, s.smtp_password)
    ):
        errors.append(
            "SMTP_SERVER, SMTP_USERNAME and SMTP_PASSWORD are required in "
            "production (verification and password reset send email)"
        )
    if not s.log_to_file:
        errors.append("LOG_TO_FILE must be true in production")
    if s.billing_provider == "dev":
        errors.append("BILLING_PROVIDER=dev accepts fake receipts; never in production")
    if s.admin_enabled and len(s.admin_dashboard_path) < 17:
        # An obvious path invites credential stuffing against the console.
        errors.append(
            "ADMIN_DASHBOARD_PATH must be at least 16 characters after the / in "
            "production; use e.g. /ops-$(openssl rand -hex 8)"
        )
    return errors


_override: Settings | None = None


@lru_cache(maxsize=1)
def _loaded() -> Settings:
    return load_settings()


def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    return _override or _loaded()


@contextmanager
def override_settings(**changes: object) -> Iterator[Settings]:
    """Temporarily replace settings fields. For tests only."""
    global _override  # pylint: disable=global-statement
    previous = _override
    _override = replace(get_settings(), **changes)  # type: ignore[arg-type]
    try:
        yield _override
    finally:
        _override = previous
