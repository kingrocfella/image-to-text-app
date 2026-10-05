"""Google Play billing: live subscription state (``purchases.subscriptionsv2``)
and the refund poll (``purchases.voidedpurchases``). Ported from Lost Vowels.

A purchase token alone proves nothing; only Google's answer does.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from app.services.billing.types import UnverifiedPurchaseError, VerifiedPurchase

_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_ORIGIN = "https://androidpublisher.googleapis.com"
_TIMEOUT_S = 10
# Bounds pagination so a malformed page token cannot loop forever.
_MAX_VOIDED_PAGES = 100
# Keeps a subscriber on Pro while Google retries a failed renewal: Google may
# leave expiryTime at the lapsed period's end during grace, so each hourly
# refresh pushes access a little past now until the retry succeeds or fails.
_GRACE_EXTENSION = timedelta(hours=2)
# States that describe a real subscription, granted or not. On hold, paused
# and expired report a past expiryTime: recorded, not granted. PENDING is
# missing on purpose: Google forbids granting before payment completes.
_RECORDABLE_STATES = {
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_CANCELED",  # auto-renew off; access runs to expiryTime
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
    "SUBSCRIPTION_STATE_ON_HOLD",
    "SUBSCRIPTION_STATE_PAUSED",
    "SUBSCRIPTION_STATE_EXPIRED",
}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GooglePlayBillingClient:
    def __init__(
        self,
        *,
        package_name: str,
        service_account_json: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(service_account_json), scopes=[_SCOPE]
        )
        self._session = AuthorizedSession(credentials)
        self._app_path = (
            f"/androidpublisher/v3/applications/{quote(package_name, safe='')}"
        )
        self._now = now

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._session.get(_ORIGIN + path, params=params, timeout=_TIMEOUT_S)

    def _verify_sync(self, token: str) -> VerifiedPurchase:
        response = self._get(
            f"{self._app_path}/purchases/subscriptionsv2/tokens/{quote(token, safe='')}"
        )
        if response.status_code in (404, 410):
            raise UnverifiedPurchaseError("purchase token unknown to Google Play")
        if not response.ok:
            raise RuntimeError(f"play_subscriptionsv2_{response.status_code}")
        body = response.json()
        state = body.get("subscriptionState") or ""
        if state not in _RECORDABLE_STATES:
            raise UnverifiedPurchaseError(f"subscription state {state or 'missing'}")
        # After a plan change there can be several line items; the latest-
        # expiring one is what the user has now.
        product_id, expires_at = "", None
        for item in body.get("lineItems") or []:
            expiry = _parse_time(item.get("expiryTime"))
            if (
                item.get("productId")
                and expiry
                and (not expires_at or expiry > expires_at)
            ):
                product_id, expires_at = item["productId"], expiry
        if not product_id or not expires_at:
            raise UnverifiedPurchaseError("subscription has no usable line item")
        if state == "SUBSCRIPTION_STATE_IN_GRACE_PERIOD":
            expires_at = max(expires_at, self._now() + _GRACE_EXTENSION)
        return VerifiedPurchase(
            # purchaseToken is stable across renewals of one subscription.
            transaction_id=token,
            product_id=product_id,
            purchased_at=_parse_time(body.get("startTime")) or self._now(),
            expires_at=expires_at,
            # Refunds arrive through the voided poll, which re-reads this state.
            revoked=False,
            # A live answer, not a replayable receipt: may move expiry back.
            authoritative=True,
        )

    def _voided_sync(self) -> list[str]:
        tokens: list[str] = []
        page_token = ""
        for _ in range(_MAX_VOIDED_PAGES):
            # type=1 includes subscriptions; the default silently omits them.
            params = {"type": "1"}
            if page_token:
                params["token"] = page_token
            response = self._get(f"{self._app_path}/purchases/voidedpurchases", params)
            if not response.ok:
                raise RuntimeError(f"play_voidedpurchases_{response.status_code}")
            body = response.json()
            tokens += [
                entry["purchaseToken"]
                for entry in body.get("voidedPurchases") or []
                if entry.get("purchaseToken")
            ]
            page_token = (body.get("tokenPagination") or {}).get("nextPageToken") or ""
            if not page_token:
                break
        return tokens

    async def verify_subscription(self, token: str) -> VerifiedPurchase:
        return await asyncio.to_thread(self._verify_sync, token)

    async def list_voided_purchase_tokens(self) -> list[str]:
        return await asyncio.to_thread(self._voided_sync)
