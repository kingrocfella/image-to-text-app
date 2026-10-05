"""Shared shapes for ScanGenAI Pro billing (ported from Lost Vowels / Letterbolt).

Kept apart from the store verifiers so the service, routes and tests depend
on these types rather than on a store SDK.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

StorePlatform = Literal["ios", "android"]


@dataclass(frozen=True)
class VerifiedPurchase:
    """What a store says about one subscription. Deliberately small."""

    # The store's STABLE subscription ID (Apple originalTransactionId, Google
    # purchaseToken), so every renewal lands on the same row.
    transaction_id: str
    product_id: str
    # When the billing period described here was bought.
    purchased_at: datetime
    # When this period's access ends; None means the store reported none.
    expires_at: datetime | None
    revoked: bool
    # True for the store's live, current state (Google's API answer); False for
    # a replayable receipt describing one period (an Apple signed transaction).
    # Only an authoritative answer may move expiry backwards.
    authoritative: bool


class UnverifiedPurchaseError(Exception):
    """The store did not confirm the purchase (forged, foreign, unknown, pending)."""


class StoreVerifier(Protocol):
    """Verifies one receipt sent by the app with the store that sold it."""

    async def verify(
        self, platform: StorePlatform, receipt: str
    ) -> VerifiedPurchase: ...


class DevStoreVerifier:
    """Deterministic stand-in for development and tests. Never in production.

    Accepts receipts shaped ``product:transaction[:revoked|:expired]``; a plain
    one is a subscription with a month left.
    """

    async def verify(self, platform: StorePlatform, receipt: str) -> VerifiedPurchase:
        product_id, _, rest = receipt.partition(":")
        transaction_id, _, flag = rest.partition(":")
        if not product_id or not transaction_id:
            raise UnverifiedPurchaseError("malformed dev receipt")
        now = datetime.now(timezone.utc)
        expires = (
            now - timedelta(hours=1) if flag == "expired" else now + timedelta(days=30)
        )
        return VerifiedPurchase(
            transaction_id=transaction_id,
            product_id=product_id,
            purchased_at=now,
            expires_at=expires,
            revoked=flag == "revoked",
            authoritative=False,
        )
