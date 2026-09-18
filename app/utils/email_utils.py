"""Transactional email rendering and delivery."""

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.utils.logger import logger

load_dotenv()

BRAND_NAME = "ScanGenAI"
COMPANY_NAME = "Leon Frontier"
SENDER_NAME = f"{COMPANY_NAME} {BRAND_NAME}"

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


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is not set")
    return value


def send_email(to: str, subject: str, html_body: str, text_body: str) -> None:
    """Send a multipart (plain text + HTML) email over SMTP with STARTTLS.

    Missing SMTP configuration raises; delivery failures are logged, not raised.
    """
    smtp_server = _required_env("SMTP_SERVER")
    smtp_port = int(_required_env("SMTP_PORT"))
    smtp_username = _required_env("SMTP_USERNAME")
    smtp_password = _required_env("SMTP_PASSWORD")

    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, smtp_username))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to send email %r: %s", subject, type(exc).__name__, exc_info=True
        )


def send_verification_email(
    email: str, verification_token: str, name: Optional[str] = None
) -> None:
    """Send the account verification email to a newly registered user."""
    app_url = _required_env("APP_URL").rstrip("/")
    context = {
        "name": name,
        "verification_url": f"{app_url}/auth/verify-email?token={verification_token}",
    }
    send_email(
        to=email,
        subject=f"Verify your email for {BRAND_NAME}",
        html_body=render_template("emails/verify_email.html", **context),
        text_body=render_template("emails/verify_email.txt", **context),
    )
