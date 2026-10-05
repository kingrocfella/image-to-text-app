"""The read-only operational picture of the running service.

One snapshot, two consumers — the admin console renders it and the operations
monitor alerts on it — because a console showing different numbers from the
job that emails you is worse than having neither. (Ported from NoAlibi, which
ported it from Letterbolt's ``internal/ops``.)
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.job_runs import STATUS_FAILED, STATUS_QUEUED
from app.services.quota import current_period, quota_limits

Severity = Literal["warning", "critical"]

# Thresholds that are not worth an env key. docs/operations.md explains each.
JOB_STALL = timedelta(minutes=30)
JOB_FAILURES_WARNING = 10  # failed in the last 24 hours
JOB_FAILURE_RATE_WARNING = 0.25  # of the jobs started in the last 24 hours
JOB_FAILURE_RATE_MIN_JOBS = 20


@dataclass(frozen=True)
class Alert:
    """One thing that is wrong, in words an operator can act on."""

    severity: Severity
    subject: str
    detail: str


@dataclass(frozen=True)
class Snapshot:
    taken_at: datetime
    users_total: int = 0
    users_verified: int = 0
    signups_7d: int = 0
    jobs_24h: int = 0
    jobs_failed_24h: int = 0
    jobs_stuck: int = 0
    pdf_collections: int = 0
    users_at_quota: int = 0
    last_backup_at: datetime | None = None
    last_backup_bytes: int | None = None
    alerts: list[Alert] = field(default_factory=list)


_COUNTS = text("""
    SELECT
      (SELECT count(*) FROM users) AS users_total,
      (SELECT count(*) FROM users WHERE is_verified) AS users_verified,
      (SELECT count(*) FROM users WHERE created_at > :week_ago) AS signups_7d,
      (SELECT count(*) FROM job_runs WHERE created_at > :day_ago) AS jobs_24h,
      (SELECT count(*) FROM job_runs
        WHERE status = :failed AND created_at > :day_ago) AS jobs_failed_24h,
      (SELECT count(*) FROM job_runs
        WHERE status = :queued AND created_at < :stall_cutoff
          AND created_at > :week_ago) AS jobs_stuck,
      (SELECT count(*) FROM pdf_requests) AS pdf_collections
    """)
_AT_QUOTA = text("""
    SELECT count(DISTINCT user_id) FROM usage_counters
    WHERE period = :period AND (
      (kind = 'image' AND count >= :image) OR (kind = 'sound' AND count >= :sound)
      OR (kind = 'pdf' AND count >= :pdf) OR (kind = 'cloud_model' AND count >= :cloud)
    )
    """)
_LAST_BACKUP = text(
    "SELECT finished_at, size_bytes FROM backup_runs ORDER BY finished_at DESC LIMIT 1"
)


async def take_snapshot(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> Snapshot:
    """Measure everything, then derive the alerts from the measurements."""
    now = now or datetime.now(timezone.utc)
    counts = (
        await session.execute(
            _COUNTS,
            {
                "week_ago": now - timedelta(days=7),
                "day_ago": now - timedelta(days=1),
                "stall_cutoff": now - JOB_STALL,
                "failed": STATUS_FAILED,
                "queued": STATUS_QUEUED,
            },
        )
    ).one()
    limits = quota_limits(settings)
    at_quota = (
        await session.execute(
            _AT_QUOTA,
            {
                "period": current_period(now),
                "image": max(limits["image"], 1),
                "sound": max(limits["sound"], 1),
                "pdf": max(limits["pdf"], 1),
                "cloud": max(limits["cloud_model"], 1),
            },
        )
    ).scalar_one()
    backup = (await session.execute(_LAST_BACKUP)).first()
    measured: dict[str, Any] = {k: int(v) for k, v in counts._mapping.items()}
    snapshot = Snapshot(
        taken_at=now,
        users_at_quota=int(at_quota),
        last_backup_at=backup.finished_at if backup else None,
        last_backup_bytes=int(backup.size_bytes) if backup else None,
        **measured,
    )
    return replace(snapshot, alerts=evaluate(snapshot, settings))


def evaluate(snapshot: Snapshot, settings: Settings) -> list[Alert]:
    """Turn measurements into alerts. Pure, so every rule is unit-testable."""
    alerts: list[Alert] = []
    max_age = timedelta(hours=settings.backup_max_age_hours)
    if snapshot.last_backup_at is None:
        alerts.append(
            Alert(
                "critical",
                "backups",
                "No verified backup has ever been recorded. Run `make backup` and "
                "install the daily cron with `make backup-cron`.",
            )
        )
    elif snapshot.taken_at - snapshot.last_backup_at > max_age:
        age_hours = int(
            (snapshot.taken_at - snapshot.last_backup_at).total_seconds() // 3600
        )
        alerts.append(
            Alert(
                "critical",
                "backups",
                f"The newest verified backup is {age_hours} hours old (limit "
                f"{settings.backup_max_age_hours}). Check `make backup-log`.",
            )
        )
    if snapshot.jobs_stuck:
        alerts.append(
            Alert(
                "critical",
                "jobs-stuck",
                f"{snapshot.jobs_stuck} job(s) have been queued for more than "
                f"{int(JOB_STALL.total_seconds() // 60)} minutes: the worker may be "
                "down or wedged. Check `make ps` and `make worker-logs`.",
            )
        )
    failure_rate = (
        snapshot.jobs_failed_24h / snapshot.jobs_24h if snapshot.jobs_24h else 0.0
    )
    if snapshot.jobs_failed_24h >= JOB_FAILURES_WARNING or (
        snapshot.jobs_24h >= JOB_FAILURE_RATE_MIN_JOBS
        and failure_rate >= JOB_FAILURE_RATE_WARNING
    ):
        alerts.append(
            Alert(
                "warning",
                "job-failures",
                f"{snapshot.jobs_failed_24h} of {snapshot.jobs_24h} jobs failed in "
                "the last 24 hours (OCR, transcription, a model provider or "
                "Qdrant). See errors.log: `make error-files`.",
            )
        )
    return alerts
