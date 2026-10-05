"""ScanGenAI Pro: recording what the stores report, and answering the question the
rest of the app asks — is this user on Pro?

The app's opinion of its own entitlement is never trusted: every purchase is
verified with the store that sold it. Ported from Lost Vowels' billing
service, including Letterbolt's review fixes.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, resolve_server_path
from app.services.billing.apple import AppleStoreKitVerifier
from app.services.billing.google import GooglePlayBillingClient
from app.services.billing.plans import PRO_PRODUCT_IDS
from app.services.billing.types import (
    DevStoreVerifier,
    StorePlatform,
    StoreVerifier,
    UnverifiedPurchaseError,
    VerifiedPurchase,
)
from app.utils.logger import logger


class WrongProductError(Exception):
    """The receipt verified, but for a product this app does not sell."""


@dataclass(frozen=True)
class BillingDeps:
    store: StoreVerifier | None
    apple: AppleStoreKitVerifier | None
    google: GooglePlayBillingClient | None


class _StoreVerifier:
    def __init__(
        self, apple: AppleStoreKitVerifier, google: GooglePlayBillingClient
    ) -> None:
        self._apple = apple
        self._google = google

    async def verify(self, platform: StorePlatform, receipt: str) -> VerifiedPurchase:
        if platform == "ios":
            return await self._apple.verify_transaction(receipt)
        return await self._google.verify_subscription(receipt)


@lru_cache(maxsize=1)
def billing_deps() -> BillingDeps:
    """The configured verifiers. ``off`` has none: purchases are refused."""
    settings = get_settings()
    if settings.billing_provider == "dev":
        return BillingDeps(store=DevStoreVerifier(), apple=None, google=None)
    if settings.billing_provider != "store":
        return BillingDeps(store=None, apple=None, google=None)
    apple = AppleStoreKitVerifier(
        root_pem=resolve_server_path(settings.apple_root_ca_path).read_bytes(),
        bundle_id=settings.ios_bundle_id,
        environment=settings.apple_environment,
        app_apple_id=settings.apple_app_id,
    )
    google = GooglePlayBillingClient(
        package_name=settings.android_package_name,
        service_account_json=settings.google_play_service_account_json,
    )
    return BillingDeps(store=_StoreVerifier(apple, google), apple=apple, google=google)


# One statement so concurrent verifications cannot interleave. The rules:
# - A receipt (Apple's signed transaction describes one period and can be
#   replayed) only ever moves expires_at FORWARD, so an old period cannot
#   shorten a paid subscription.
# - An AUTHORITATIVE answer (Google's live state) replaces expiry and product
#   outright; that is how a Google revocation ends access.
# - After a refund, only a period bought AFTER the revocation lifts it, and it
#   brings its own expiry; otherwise a replayed pre-refund receipt would be
#   handed back for free.
# - With an owner (a signed-in request from the app) ownership moves to that
#   user; without one (a store notification or the refresh job) it is kept.
_RECORD = text("""
    INSERT INTO purchases (platform, product_id, transaction_id, user_id,
                           purchased_at, expires_at)
    VALUES (:platform, :product_id, :transaction_id, :user_id,
            :purchased_at, :expires_at)
    ON CONFLICT (platform, transaction_id) DO UPDATE SET
      user_id = CASE WHEN :has_owner THEN excluded.user_id ELSE purchases.user_id END,
      product_id = CASE
        WHEN purchases.revoked_at IS NOT NULL
             AND excluded.purchased_at > purchases.revoked_at THEN excluded.product_id
        WHEN :authoritative OR purchases.expires_at IS NULL
             OR excluded.expires_at > purchases.expires_at THEN excluded.product_id
        ELSE purchases.product_id END,
      expires_at = CASE
        WHEN purchases.revoked_at IS NOT NULL
             AND excluded.purchased_at > purchases.revoked_at THEN excluded.expires_at
        WHEN :authoritative THEN excluded.expires_at
        ELSE GREATEST(purchases.expires_at, excluded.expires_at) END,
      revocation_reason = CASE
        WHEN purchases.revoked_at IS NOT NULL
             AND excluded.purchased_at > purchases.revoked_at THEN NULL
        ELSE purchases.revocation_reason END,
      revoked_at = CASE
        WHEN purchases.revoked_at IS NOT NULL
             AND excluded.purchased_at > purchases.revoked_at THEN NULL
        ELSE purchases.revoked_at END,
      purchased_at = GREATEST(purchases.purchased_at, excluded.purchased_at),
      updated_at = now()
    RETURNING (revoked_at IS NULL AND expires_at IS NOT NULL AND expires_at > now())
      AS active
    """)
_REVOKE = text("""
    UPDATE purchases SET revoked_at = now(), revocation_reason = :reason,
                         updated_at = now()
    WHERE platform = :platform AND transaction_id = :transaction_id
      AND revoked_at IS NULL
    RETURNING id
    """)
_UNREVOKE = text("""
    UPDATE purchases SET revoked_at = NULL, revocation_reason = NULL, updated_at = now()
    WHERE platform = :platform AND transaction_id = :transaction_id
    """)


async def _record(
    db: AsyncSession,
    owner: UUID | None,
    platform: StorePlatform,
    purchase: VerifiedPurchase,
) -> bool:
    if purchase.revoked:
        await revoke_purchase(
            db, platform, purchase.transaction_id, "store reports the purchase revoked"
        )
        return False
    row = (
        await db.execute(
            _RECORD,
            {
                "platform": platform,
                "product_id": purchase.product_id,
                "transaction_id": purchase.transaction_id,
                "user_id": owner,
                "purchased_at": purchase.purchased_at,
                "expires_at": purchase.expires_at,
                "has_owner": owner is not None,
                "authoritative": purchase.authoritative,
            },
        )
    ).one()
    await db.commit()
    return bool(row.active)


async def redeem_purchase(
    db: AsyncSession,
    verifier: StoreVerifier,
    user_id: UUID,
    platform: StorePlatform,
    receipt: str,
) -> bool:
    """Verify a receipt from the app and attach it to the user.

    Returns whether the subscription is active now; an expired one is recorded
    but grants nothing.
    """
    purchase = await verifier.verify(platform, receipt)
    if purchase.product_id not in PRO_PRODUCT_IDS:
        raise WrongProductError(purchase.product_id)
    if not purchase.revoked and purchase.expires_at is None:
        raise UnverifiedPurchaseError("subscription has no expiry")
    active = await _record(db, user_id, platform, purchase)
    logger.info(
        "Purchase redeemed platform=%s product=%s active=%s",
        platform,
        purchase.product_id,
        active,
    )
    return active


async def apply_store_update(
    db: AsyncSession, platform: StorePlatform, purchase: VerifiedPurchase
) -> bool:
    """Record what a store reported with no user present (an Apple notification
    or the Android refresh). Never assigns an owner."""
    if purchase.product_id not in PRO_PRODUCT_IDS:
        return False
    # A revocation needs only the identity; anything else without an expiry
    # (a superseded upgrade transaction) has nothing to extend.
    if not purchase.revoked and purchase.expires_at is None:
        return False
    return await _record(db, None, platform, purchase)


async def revoke_purchase(
    db: AsyncSession, platform: StorePlatform, transaction_id: str, reason: str
) -> bool:
    """Refund or chargeback. Idempotent: stores retry on any non-2xx."""
    rows = (
        await db.execute(
            _REVOKE,
            {"platform": platform, "transaction_id": transaction_id, "reason": reason},
        )
    ).all()
    await db.commit()
    if rows:
        logger.info("Purchase revoked platform=%s reason=%s", platform, reason)
    return bool(rows)


async def reverse_refund(
    db: AsyncSession, platform: StorePlatform, purchase: VerifiedPurchase
) -> None:
    """Apple reversed a refund (REFUND_REVERSED).

    The reinstated transaction was bought before the revocation, so the "bought
    after the refund" rule could never lift it: clear it, then record it.
    """
    if purchase.product_id not in PRO_PRODUCT_IDS:
        return
    await db.execute(
        _UNREVOKE, {"platform": platform, "transaction_id": purchase.transaction_id}
    )
    await db.commit()
    logger.info("Purchase reinstated after refund reversal platform=%s", platform)
    await apply_store_update(
        db,
        platform,
        VerifiedPurchase(
            purchase.transaction_id,
            purchase.product_id,
            purchase.purchased_at,
            purchase.expires_at,
            False,
            purchase.authoritative,
        ),
    )


# Android refresh window. Google renews when a period ends and the job runs
# hourly, so looking an hour ahead catches each renewal on the first run after
# it; the look-back covers a failed payment Google recovers after expiry.
REFRESH_BATCH_SIZE = 500
_DUE_FOR_REFRESH = text("""
    SELECT transaction_id FROM purchases
    WHERE platform = 'android' AND revoked_at IS NULL
      AND expires_at > :lookback AND expires_at < :lookahead
    ORDER BY checked_at ASC NULLS FIRST, expires_at
    LIMIT :batch
    """)
_MARK_CHECKED = text(
    "UPDATE purchases SET checked_at = now() "
    "WHERE platform = 'android' AND transaction_id = :transaction_id"
)


async def refresh_google_subscriptions(
    db: AsyncSession,
    google: GooglePlayBillingClient | None,
    now: datetime | None = None,
    batch_size: int = REFRESH_BATCH_SIZE,
) -> tuple[int, int]:
    """Re-read Android subscriptions near or just past expiry.

    Play pushes nothing to us, so without this a renewing subscriber would
    lose Pro at the end of every period. Least recently checked first, and each
    row is marked before Google is called, so a backlog cannot starve a newer
    renewal. Best-effort per row; the first unexpected error is re-raised.
    """
    if google is None or batch_size <= 0:
        return (0, 0)
    now = now or datetime.now(timezone.utc)
    tokens = (
        (
            await db.execute(
                _DUE_FOR_REFRESH,
                {
                    "lookback": now - timedelta(days=7),
                    "lookahead": now + timedelta(hours=1),
                    "batch": batch_size,
                },
            )
        )
        .scalars()
        .all()
    )
    checked = active = 0
    first_error: Exception | None = None
    for token in tokens:
        checked += 1
        await db.execute(_MARK_CHECKED, {"transaction_id": token})
        await db.commit()
        try:
            if await apply_store_update(
                db, "android", await google.verify_subscription(token)
            ):
                active += 1
        except UnverifiedPurchaseError:
            continue  # unknown or pending: the row expires on schedule
        except Exception as exc:  # noqa: BLE001
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    return (checked, active)


async def sweep_google_voided(
    db: AsyncSession, google: GooglePlayBillingClient | None
) -> tuple[int, int]:
    """Poll Google's refunded and charged-back purchases.

    A voided entry does NOT mean the subscription is gone: refunding one past
    month leaves the current paid period, and one token spans every period.
    Google's live state is the truth; only a token Google no longer recognises
    is revoked (fail closed).
    """
    if google is None:
        return (0, 0)
    tokens = await google.list_voided_purchase_tokens()
    ended = 0
    first_error: Exception | None = None
    for token in tokens:
        try:
            purchase = await google.verify_subscription(token)
            if not await apply_store_update(db, "android", purchase):
                ended += 1
        except UnverifiedPurchaseError:
            await revoke_purchase(db, "android", token, "google:voided-unknown-token")
            ended += 1
        except Exception as exc:  # noqa: BLE001
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    return (len(tokens), ended)
