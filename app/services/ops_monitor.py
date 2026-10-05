"""Operations monitor: mail the operator when something needs a human.

Runs in the API process every OPS_CHECK_INTERVAL_MINUTES (the worker is a
Dramatiq process with no event loop of its own, and Compose runs one API
process). An alert is mailed when
it starts, again every OPS_ALERT_REPEAT_HOURS while it lasts, and once more
when it resolves. ``ops_alert_state`` remembers what was sent, so a restart
does not re-mail everything.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database.postgres import AsyncSessionLocal
from app.services.notify import send_operator_email
from app.services.ops import Alert, take_snapshot
from app.utils.logger import logger

_OPEN_ALERTS = text(
    "SELECT subject, last_notified_at FROM ops_alert_state WHERE resolved_at IS NULL"
)
_UPSERT_ALERT = text("""
    INSERT INTO ops_alert_state (subject, severity, detail, first_seen_at)
    VALUES (:subject, :severity, :detail, :now)
    ON CONFLICT (subject) DO UPDATE SET
      severity = EXCLUDED.severity,
      detail = EXCLUDED.detail,
      first_seen_at = CASE WHEN ops_alert_state.resolved_at IS NULL
                           THEN ops_alert_state.first_seen_at ELSE :now END,
      last_notified_at = CASE WHEN ops_alert_state.resolved_at IS NULL
                              THEN ops_alert_state.last_notified_at END,
      resolved_at = NULL
    """)
_MARK_NOTIFIED = text(
    "UPDATE ops_alert_state SET last_notified_at = :now WHERE subject = :subject"
)
_RESOLVE = text(
    "UPDATE ops_alert_state SET resolved_at = :now "
    "WHERE subject = :subject AND resolved_at IS NULL"
)


def _alert_body(alert: Alert) -> str:
    return (
        f"{alert.severity.upper()}: {alert.subject}\n\n{alert.detail}\n\n"
        "This repeats while the problem lasts; you will get one more email when "
        "it clears. See docs/operations.md for what each alert means.\n"
    )


async def run_ops_check(
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    now: datetime | None = None,
) -> list[Alert]:
    """Take one snapshot and send whatever emails it calls for."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    repeat_after = timedelta(hours=settings.ops_alert_repeat_hours)

    async with session_factory() as session:
        snapshot = await take_snapshot(session, settings, now)
        previously_open = {
            row.subject: row.last_notified_at
            for row in (await session.execute(_OPEN_ALERTS)).all()
        }
        for alert in snapshot.alerts:
            await session.execute(
                _UPSERT_ALERT,
                {
                    "subject": alert.subject,
                    "severity": alert.severity,
                    "detail": alert.detail,
                    "now": now,
                },
            )
        current = {alert.subject for alert in snapshot.alerts}
        for subject in previously_open.keys() - current:
            await session.execute(_RESOLVE, {"subject": subject, "now": now})
        await session.commit()

        for alert in snapshot.alerts:
            last = previously_open.get(alert.subject)
            if last is not None and now - last < repeat_after:
                continue
            sent = await send_operator_email(
                f"{alert.severity}: {alert.subject}", _alert_body(alert)
            )
            if sent:
                await session.execute(
                    _MARK_NOTIFIED, {"subject": alert.subject, "now": now}
                )
        for subject in previously_open.keys() - current:
            if previously_open[subject] is not None:
                await send_operator_email(
                    f"resolved: {subject}", f"{subject} is back to normal.\n"
                )
        await session.commit()

    for alert in snapshot.alerts:
        log = logger.error if alert.severity == "critical" else logger.warning
        log("Ops alert %s: %s", alert.subject, alert.detail)
    return snapshot.alerts


async def ops_monitor_loop() -> None:
    """Background loop. Errors are logged and retried on the next tick."""
    interval = get_settings().ops_check_interval_minutes * 60
    while True:
        try:
            await run_ops_check()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Operations check failed", exc_info=True)
        await asyncio.sleep(interval)
