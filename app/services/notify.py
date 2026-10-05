"""Operator notification email (ported from Letterbolt's ``internal/notify``).

This is not user email: the only recipient is NOTIFY_EMAIL_TO, the operator's
inbox. Three properties matter more than delivery:

- A notification never fails the work that triggered it; the worst outcome
  is a log line.
- It is off by default (NOTIFY_EMAILS_ENABLED=false), so development and the
  test suite cannot mail anybody.
- Subjects are header-safe: a value containing a line break is refused.
"""

import asyncio

from app.config import get_settings
from app.utils.email_utils import send_plain_email
from app.utils.logger import logger

SUBJECT_PREFIX = "[ScanGenAI ops]"


async def send_operator_email(subject: str, body: str) -> bool:
    """Mail the operator. Returns True only if a message was handed to SMTP."""
    settings = get_settings()
    if not settings.notify_emails_enabled:
        return False
    if "\r" in subject or "\n" in subject:
        logger.error("Operator email refused: subject contains a line break")
        return False
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                send_plain_email,
                settings.notify_email_to,
                f"{SUBJECT_PREFIX} {subject}",
                body,
            ),
            timeout=30,
        )
    except Exception:
        logger.error("Operator email failed subject=%r", subject, exc_info=True)
        return False
    return True
