"""ScanGenAI Pro billing endpoints (docs/billing.md).

The app buys through the store, then sends the receipt here; the server asks
the store and decides the entitlement. Apple also calls in with renewals and
refunds; Google is polled by a background loop.
"""

from dataclasses import replace
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User, get_db
from app.dependencies import get_current_active_user
from app.paths import Api, Web
from app.routes.me import build_me
from app.schemas import MeResponse
from app.services.billing.service import (
    WrongProductError,
    apply_store_update,
    billing_deps,
    redeem_purchase,
    reverse_refund,
)
from app.services.billing.types import UnverifiedPurchaseError
from app.utils.logger import logger
from app.utils.rate_limit import limiter

router = APIRouter(tags=["billing"])
# Server-to-server: registered unversioned and outside the app-version gate.
webhook_router = APIRouter(include_in_schema=False)

StorePlatformField = Literal["ios", "android"]

# Apple notification types that end access: refund, chargeback, and Family
# Sharing revoked.
_APPLE_REVOCATIONS = {"REFUND", "REVOKE"}


class VerifyPurchaseRequest(BaseModel):
    platform: StorePlatformField
    # A StoreKit 2 signed transaction (JWS) or a Play purchase token.
    receipt: str = Field(..., min_length=1, max_length=20_000)


class RecoverPurchasesRequest(BaseModel):
    platform: StorePlatformField
    receipts: list[str] = Field(..., min_length=1, max_length=20)


def _user_id(user: User) -> UUID:
    return cast(UUID, user.id)


def _store():
    store = billing_deps().store
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Subscriptions are not available yet.",
        )
    return store


@router.post(Api.BILLING_VERIFY, response_model=MeResponse)
@limiter.limit("20/minute")
async def verify_purchase(
    request: Request,  # pylint: disable=unused-argument
    body: VerifyPurchaseRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """Verify one receipt with its store and attach it to the caller."""
    store = _store()
    try:
        await redeem_purchase(
            db, store, _user_id(current_user), body.platform, body.receipt
        )
    except UnverifiedPurchaseError as exc:
        logger.info("Store did not confirm a purchase: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The store did not confirm this purchase.",
        ) from exc
    except WrongProductError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This purchase is for a different product.",
        ) from exc
    return await build_me(db, current_user)


@router.post(Api.BILLING_RECOVER, response_model=MeResponse)
@limiter.limit("10/minute")
async def recover_purchases(
    request: Request,  # pylint: disable=unused-argument
    body: RecoverPurchasesRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """Restore Purchases. Best-effort per receipt: one stale or foreign receipt
    must not fail the restore; only an infrastructure error does."""
    store = _store()
    for receipt in body.receipts:
        try:
            await redeem_purchase(
                db, store, _user_id(current_user), body.platform, receipt
            )
        except (UnverifiedPurchaseError, WrongProductError):
            logger.info("Restore skipped a receipt the store did not confirm")
    return await build_me(db, current_user)


class AppleNotificationBody(BaseModel):
    signedPayload: str = Field(..., min_length=1, max_length=200_000)  # noqa: N815


@webhook_router.post(Web.APPLE_NOTIFICATIONS)
async def apple_notification(
    body: AppleNotificationBody, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    """App Store Server Notifications V2.

    Self-authenticating: only Apple can produce a payload that chains to the
    pinned root. Renewals extend, refunds revoke, REFUND_REVERSED reinstates.
    A database failure answers 500, so Apple retries instead of losing it.
    """
    apple = billing_deps().apple
    if apple is None:
        logger.warning("Apple notification received while store billing is off")
        return {"ok": True}
    try:
        notification = await apple.verify_notification(body.signedPayload)
    except UnverifiedPurchaseError as exc:
        logger.warning("Apple notification failed verification")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature.") from exc
    purchase = notification.purchase
    if notification.notification_type == "REFUND_REVERSED":
        await reverse_refund(db, "ios", purchase)
    else:
        # A REVOKE (Family Sharing ended) can carry a transaction with no
        # revocationDate, so mark it explicitly rather than trust the flag.
        if (
            notification.notification_type in _APPLE_REVOCATIONS
            and not purchase.revoked
        ):
            purchase = replace(purchase, revoked=True)
        await apply_store_update(db, "ios", purchase)
    logger.info(
        "Apple notification applied type=%s subtype=%s",
        notification.notification_type,
        notification.subtype,
    )
    return {"ok": True}
