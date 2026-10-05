"""app/config.py is the only environment reader, and it fails closed."""

import re
from pathlib import Path

import pytest

from app.config import ConfigError, load_settings

BASE = {
    "SECRET_KEY": "k" * 40,
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p",
    "POSTGRES_DB": "d",
    "POSTGRES_HOST": "h",
}
PRODUCTION = {
    **BASE,
    "ENVIRONMENT": "production",
    "APP_URL": "https://api.example.com",
    "SMTP_SERVER": "smtp.example.com",
    "SMTP_USERNAME": "no-reply@example.com",
    "SMTP_PASSWORD": "pw",
    "LOG_TO_FILE": "true",
}


def _errors(environ: dict[str, str]) -> str:
    with pytest.raises(ConfigError) as raised:
        load_settings(environ)
    return str(raised.value)


def test_defaults_load_in_dev():
    settings = load_settings(BASE)
    assert settings.environment == "dev"
    assert not settings.is_production
    assert settings.refresh_token_expire_days == 30


@pytest.mark.parametrize("value", ["prod", "development", "Production", "test"])
def test_a_mistyped_environment_stops_startup(value):
    """A typo used to mean "not production", silently dropping HSTS and more."""
    assert "ENVIRONMENT must be dev or production" in _errors(
        {**BASE, "ENVIRONMENT": value}
    )


def test_production_loads_with_everything_it_needs():
    assert load_settings(PRODUCTION).is_production


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"APP_URL": "http://api.example.com"}, "APP_URL must use https"),
        ({"APP_URL": ""}, "APP_URL is required"),
        ({"SMTP_PASSWORD": ""}, "SMTP_SERVER, SMTP_USERNAME and SMTP_PASSWORD"),
        ({"SMTP_SERVER": "change-me"}, "SMTP_SERVER, SMTP_USERNAME and SMTP_PASSWORD"),
        ({"LOG_TO_FILE": "false"}, "LOG_TO_FILE must be true"),
        (
            {"ADMIN_DASHBOARD_TOKEN": "t" * 40, "ADMIN_DASHBOARD_PATH": "/admin"},
            "ADMIN_DASHBOARD_PATH must be at least 16 characters",
        ),
    ],
)
def test_production_refuses_unsafe_values(change, message):
    assert message in _errors({**PRODUCTION, **change})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"CORS_ALLOWED_ORIGINS": "https://a.example,*"}, "never *"),
        ({"MINIMUM_APP_VERSION": "1.0"}, "MAJOR.MINOR.PATCH"),
        ({"REQUEST_TIMEOUT_SECONDS": "soon"}, "must be a number"),
        ({"QUOTA_IMAGE_MONTHLY": "-1"}, "at least 0"),
        ({"TRUST_PROXY_HEADERS": "maybe"}, "must be true or false"),
        ({"SECRET_KEY_PREVIOUS": "k" * 40}, "must differ from SECRET_KEY"),
        ({"ADMIN_DASHBOARD_TOKEN": "short"}, "at least 32 characters"),
        ({"NOTIFY_EMAILS_ENABLED": "true"}, "NOTIFY_EMAIL_TO"),
        ({"APP_URL": "https://api.example.com/v1"}, "origin with no path"),
    ],
)
def test_malformed_values_are_reported(change, message):
    assert message in _errors({**BASE, **change})


def test_every_problem_is_reported_at_once():
    message = _errors({"ENVIRONMENT": "nope", "POSTGRES_PORT": "x"})
    assert "SECRET_KEY is required" in message
    assert "POSTGRES_PORT must be an integer" in message
    assert "ENVIRONMENT must be dev or production" in message


def test_nothing_outside_config_reads_the_environment():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    pattern = re.compile(r"os\.(getenv|environ)|load_dotenv")
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.name != "config.py" and pattern.search(path.read_text())
    ]
    assert offenders == []
