"""Who is on ScanGenAI Pro.

Pro comes from an unexpired, unrevoked store subscription or from a grant
(``users.pro_until``). Every read fails closed: an error means "not Pro",
never free Pro. The app's own opinion of its plan is never consulted.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.billing.plans import PRO_PRODUCT_IDS
from app.utils.logger import logger

ProSource = Literal["subscription", "grant"]

# 402 Payment Required: the app opens the paywall on this status.
PRO_REQUIRED_STATUS = 402

_ACTIVE_SUBSCRIPTION = text("""
    SELECT product_id, expires_at FROM purchases
    WHERE user_id = :user_id AND revoked_at IS NULL AND expires_at > :now
      AND product_id = ANY(:products)
    ORDER BY expires_at DESC LIMIT 1
    """)
_GRANT = text("SELECT pro_until FROM users WHERE id = :user_id")


@dataclass(frozen=True)
class Entitlement:
    pro: bool
    source: ProSource | None = None
    product_id: str | None = None
    expires_at: datetime | None = None


FREE = Entitlement(pro=False)


def pro_required(detail: str) -> HTTPException:
    return HTTPException(status_code=PRO_REQUIRED_STATUS, detail=detail)


async def get_entitlement(
    db: AsyncSession, user_id: UUID, now: datetime | None = None
) -> Entitlement:
    now = now or datetime.now(timezone.utc)
    try:
        # A savepoint, so a failed read cannot poison the request's transaction.
        async with db.begin_nested():
            subscription = (
                await db.execute(
                    _ACTIVE_SUBSCRIPTION,
                    {"user_id": user_id, "now": now, "products": list(PRO_PRODUCT_IDS)},
                )
            ).first()
            grant = (
                await db.execute(_GRANT, {"user_id": user_id})
            ).scalar_one_or_none()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Entitlement read failed; treating as free", exc_info=True)
        return FREE

    if subscription is not None:
        return Entitlement(
            True, "subscription", subscription.product_id, subscription.expires_at
        )
    if grant is not None:
        if grant.tzinfo is None:
            grant = grant.replace(tzinfo=timezone.utc)
        if grant > now:
            return Entitlement(True, "grant", None, grant)
    return FREE
