"""Transactional email rendering and delivery."""

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings
from app.paths import Web
from app.utils.logger import logger

BRAND_NAME = "ScanGenAI"
COMPANY_NAME = "Leon Frontier"

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Autoescape HTML (including user-supplied values such as names); plain-text
# templates are rendered verbatim.
templates = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(template_name: str, /, **context: Any) -> str:
    """Render a template with the shared brand context."""
    return templates.get_template(template_name).render(
        brand_name=BRAND_NAME,
        company_name=COMPANY_NAME,
        year=datetime.now(timezone.utc).year,
        **context,
    )


def _deliver(to: str, subject: str, text_body: str, html_body: str | None) -> None:
    settings = get_settings()
    if not settings.smtp_configured:
        raise ValueError("SMTP_SERVER, SMTP_USERNAME and SMTP_PASSWORD must be set")

    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_username))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=20) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


def send_email(to: str, subject: str, html_body: str, text_body: str) -> None:
    """Send a multipart (plain text + HTML) email over SMTP with STARTTLS.

    Missing SMTP configuration raises; delivery failures are logged, not raised.
    """
    if not get_settings().smtp_configured:
        raise ValueError("SMTP_SERVER, SMTP_USERNAME and SMTP_PASSWORD must be set")
    try:
        _deliver(to, subject, text_body, html_body)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to send email %r: %s", subject, type(exc).__name__, exc_info=True
        )


def send_plain_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Raises on failure (operator notifications)."""
    _deliver(to, subject, body, None)


def _app_url() -> str:
    app_url = get_settings().app_url
    if not app_url:
        raise ValueError("APP_URL must be set to send account email")
    return app_url


def send_verification_email(
    email: str, verification_token: str, name: Optional[str] = None
) -> None:
    """Send the account verification email to a newly registered user."""
    context = {
        "name": name,
        "verification_url": (
            f"{_app_url()}{Web.VERIFY_EMAIL}?token={verification_token}"
        ),
        "expires_hours": get_settings().verification_token_expire_hours,
    }
    send_email(
        to=email,
        subject=f"Verify your email for {BRAND_NAME}",
        html_body=render_template("emails/verify_email.html", **context),
        text_body=render_template("emails/verify_email.txt", **context),
    )


def send_password_reset_email(
    email: str, reset_token: str, name: Optional[str] = None
) -> None:
    """Send the one-time password-reset link."""
    context = {
        "name": name,
        "reset_url": f"{_app_url()}{Web.RESET_PASSWORD_PAGE}?token={reset_token}",
        "expires_minutes": get_settings().password_reset_token_expire_minutes,
    }
    send_email(
        to=email,
        subject=f"Reset your {BRAND_NAME} password",
        html_body=render_template("emails/reset_password.html", **context),
        text_body=render_template("emails/reset_password.txt", **context),
    )
