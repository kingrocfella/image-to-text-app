"""Per-user monthly allowances (AGENTS.md §8).

OCR and transcription cost this server CPU; every PDF question costs OpenAI
embeddings, and a cloud-model answer costs that provider too. All of it is
billed to the operator, so each account gets a monthly allowance per kind of
work, counted here and nowhere else. The app may show the numbers; only this
module decides.

A count is taken in the request's own transaction, before the job is queued:
if queueing fails the request rolls back and the allowance is not spent.
"""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import UsageCounter
from app.utils.logger import logger

KIND_IMAGE = "image"
KIND_SOUND = "sound"
KIND_PDF = "pdf"
KIND_CLOUD_MODEL = "cloud_model"

_LABELS = {
    KIND_IMAGE: "image scans",
    KIND_SOUND: "audio transcriptions",
    KIND_PDF: "PDF questions",
    KIND_CLOUD_MODEL: "cloud-model answers",
}


def quota_limits(settings: Settings, pro: bool = False) -> dict[str, int]:
    """This month's allowances for a free account, or for one on Pro."""
    if pro:
        return {
            KIND_IMAGE: settings.pro_quota_image_monthly,
            KIND_SOUND: settings.pro_quota_sound_monthly,
            KIND_PDF: settings.pro_quota_pdf_monthly,
            KIND_CLOUD_MODEL: settings.pro_quota_cloud_model_monthly,
        }
    return {
        KIND_IMAGE: settings.quota_image_monthly,
        KIND_SOUND: settings.quota_sound_monthly,
        KIND_PDF: settings.quota_pdf_monthly,
        KIND_CLOUD_MODEL: settings.quota_cloud_model_monthly,
    }


def current_period(now: datetime | None = None) -> str:
    """The UTC calendar month an allowance belongs to, ``YYYY-MM``."""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _exceeded(kind: str, limit: int, pro: bool) -> HTTPException:
    if not limit:
        detail = f"{_LABELS[kind].capitalize()} are not available."
    elif pro:
        detail = (
            f"You have used all {limit} {_LABELS[kind]} for this month. "
            "The allowance resets on the 1st."
        )
    else:
        detail = (
            f"You have used all {limit} free {_LABELS[kind]} for this month. "
            "Upgrade to ScanGenAI Pro for more, or wait until the 1st."
        )
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        # The app offers the paywall when a free account hits a limit.
        headers={"X-Quota-Exceeded": kind, "X-Upgrade-Available": str(not pro).lower()},
    )


async def _take_one(
    db: AsyncSession, user_id: uuid.UUID, period: str, kind: str, limit: int
) -> bool:
    where = (
        UsageCounter.user_id == user_id,
        UsageCounter.period == period,
        UsageCounter.kind == kind,
    )
    # Conditional increment: atomic, so concurrent requests cannot overshoot.
    bumped = await db.execute(
        update(UsageCounter)
        .where(*where, UsageCounter.count < limit)
        .values(count=UsageCounter.count + 1)
    )
    if bumped.rowcount:  # type: ignore[attr-defined]
        return True
    exists = (await db.execute(select(UsageCounter.id).where(*where))).first()
    if exists:
        return False
    try:
        async with db.begin_nested():
            db.add(UsageCounter(user_id=user_id, period=period, kind=kind, count=1))
        return True
    except IntegrityError:
        # Another request created the row first; count against it instead.
        bumped = await db.execute(
            update(UsageCounter)
            .where(*where, UsageCounter.count < limit)
            .values(count=UsageCounter.count + 1)
        )
        return bool(bumped.rowcount)  # type: ignore[attr-defined]


async def consume(
    db: AsyncSession, user_id: uuid.UUID, *kinds: str, pro: bool = False
) -> None:
    """Spend one unit of each kind, or raise 429 and spend nothing.

    Nothing is committed here: the caller's transaction decides, so a later
    failure in the same request gives the allowance back.
    """
    limits = quota_limits(get_settings(), pro)
    period = current_period()
    for kind in kinds:
        limit = limits[kind]
        if limit <= 0 or not await _take_one(db, user_id, period, kind, limit):
            logger.info("Monthly allowance reached kind=%s user=%s", kind, user_id)
            await db.rollback()
            raise _exceeded(kind, limit, pro)


async def usage(
    db: AsyncSession, user_id: uuid.UUID, pro: bool = False
) -> dict[str, dict[str, int]]:
    """This month's ``{kind: {used, limit}}`` for one account."""
    rows = (
        await db.execute(
            select(UsageCounter.kind, UsageCounter.count).where(
                UsageCounter.user_id == user_id,
                UsageCounter.period == current_period(),
            )
        )
    ).all()
    used = {str(kind): int(count) for kind, count in rows}
    return {
        kind: {"used": used.get(kind, 0), "limit": limit}
        for kind, limit in quota_limits(get_settings(), pro).items()
    }
