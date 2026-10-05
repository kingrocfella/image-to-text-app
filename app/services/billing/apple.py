"""App Store signed-data verification: StoreKit 2 transactions and App Store
Server Notifications V2 (ported from Lost Vowels' apple-storekit-verifier).

A signed transaction is self-contained: its JWS header carries the whole
certificate chain (``x5c``), and it is trusted only if that chain ends at the
**pinned** Apple Root CA G3. Pinning, not the system trust store, is the
control: any publicly trusted CA could otherwise mint a "purchase".
"""

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import jwt
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ObjectIdentifier

from app.services.billing.types import UnverifiedPurchaseError, VerifiedPurchase

# Apple's marker extensions: the App Store signing leaf and the WWDR
# intermediate that issues it.
_LEAF_OID = ObjectIdentifier("1.2.840.113635.100.6.11.1")
_INTERMEDIATE_OID = ObjectIdentifier("1.2.840.113635.100.6.2.1")


@dataclass(frozen=True)
class AppleNotification:
    notification_type: str
    subtype: str
    purchase: VerifiedPurchase


def _millis(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return None


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


class AppleStoreKitVerifier:
    def __init__(
        self,
        *,
        root_pem: bytes,
        bundle_id: str,
        environment: str,
        app_apple_id: int,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._root = x509.load_pem_x509_certificate(root_pem)
        self._root_fingerprint = _fingerprint(self._root)
        self._bundle_id = bundle_id
        self._environment = environment
        self._app_apple_id = app_apple_id
        self._now = now

    def _accepts_environment(self, environment: object) -> bool:
        """A Production server also accepts Sandbox.

        App Review tests release builds with sandbox accounts, and TestFlight
        purchases are Sandbox too; refusing them fails review. Only this team's
        App Store Connect accounts can buy in Sandbox, so the public cannot use
        it to get Pro free. A Sandbox server stays sandbox-only; anything else
        (Xcode's local StoreKit testing) is refused.
        """
        return environment == self._environment or (
            self._environment == "Production" and environment == "Sandbox"
        )

    def _chain(self, jws: str) -> tuple[x509.Certificate, ...]:
        """The x5c chain from the JWS header: leaf, intermediate, root."""
        try:
            chain = jwt.get_unverified_header(jws).get("x5c")
        except jwt.InvalidTokenError as exc:
            raise UnverifiedPurchaseError("malformed signed data") from exc
        if (
            not isinstance(chain, list)
            or len(chain) != 3
            or not all(isinstance(item, str) for item in chain)
        ):
            raise UnverifiedPurchaseError(
                "signed data must carry a three-certificate chain"
            )
        try:
            return tuple(
                x509.load_der_x509_certificate(base64.b64decode(item)) for item in chain
            )
        except ValueError as exc:
            raise UnverifiedPurchaseError("unreadable certificate chain") from exc

    def _verify_signed(self, jws: str) -> dict[str, Any]:
        leaf, intermediate, chain_root = self._chain(jws)
        self._check_chain(leaf, intermediate, chain_root)
        try:
            claims = jwt.decode(
                jws,
                leaf.public_key(),  # type: ignore[arg-type]
                algorithms=["ES256"],
                options={"verify_aud": False, "verify_exp": False, "verify_iat": False},
            )
        except jwt.InvalidTokenError as exc:
            raise UnverifiedPurchaseError("signature does not verify") from exc
        return dict(claims)

    def _check_chain(
        self,
        leaf: x509.Certificate,
        intermediate: x509.Certificate,
        chain_root: x509.Certificate,
    ) -> None:
        if _fingerprint(chain_root) != self._root_fingerprint:
            raise UnverifiedPurchaseError(
                "certificate chain does not end at the pinned Apple root"
            )
        for cert, oid, what in (
            (leaf, _LEAF_OID, "leaf is not an App Store signing certificate"),
            (intermediate, _INTERMEDIATE_OID, "intermediate is not Apple WWDR"),
        ):
            try:
                cert.extensions.get_extension_for_oid(oid)
            except x509.ExtensionNotFound as exc:
                raise UnverifiedPurchaseError(what) from exc
        try:
            leaf.verify_directly_issued_by(intermediate)
            intermediate.verify_directly_issued_by(self._root)
        except Exception as exc:  # InvalidSignature, ValueError, TypeError
            raise UnverifiedPurchaseError(
                "certificate chain signatures do not verify"
            ) from exc
        now = self._now()
        for cert in (leaf, intermediate):
            if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
                raise UnverifiedPurchaseError("certificate outside its validity period")

    def _purchase(self, claims: dict[str, Any]) -> VerifiedPurchase:
        if claims.get("bundleId") != self._bundle_id:
            raise UnverifiedPurchaseError("transaction is for another app")
        if not self._accepts_environment(claims.get("environment")):
            raise UnverifiedPurchaseError(
                f"environment {claims.get('environment')!r} not accepted"
            )
        product_id = claims.get("productId")
        transaction_id = claims.get("transactionId")
        if not isinstance(product_id, str) or not product_id:
            raise UnverifiedPurchaseError("transaction is missing its product")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise UnverifiedPurchaseError("transaction is missing its identifier")
        # Every renewal is a new transactionId; originalTransactionId stays the
        # same for the whole subscription, so it is the row key.
        original = claims.get("originalTransactionId")
        subscription_id = (
            original if isinstance(original, str) and original else transaction_id
        )
        purchased_at = _millis(claims.get("purchaseDate")) or self._now()
        if claims.get("isUpgraded") is True:
            # Monthly -> yearly supersedes the old transaction, which Apple may
            # mark refunded for proration. It is not a refund of the
            # subscription, so it must neither revoke nor extend anything.
            return VerifiedPurchase(
                subscription_id, product_id, purchased_at, None, False, False
            )
        return VerifiedPurchase(
            transaction_id=subscription_id,
            product_id=product_id,
            purchased_at=purchased_at,
            expires_at=_millis(claims.get("expiresDate")),
            revoked=_millis(claims.get("revocationDate")) is not None,
            # One billing period, replayable: may only ever move expiry forward.
            authoritative=False,
        )

    async def verify_transaction(self, signed_transaction: str) -> VerifiedPurchase:
        return self._purchase(self._verify_signed(signed_transaction))

    async def verify_notification(self, signed_payload: str) -> AppleNotification:
        claims = self._verify_signed(signed_payload)
        raw_data = claims.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        if data.get("bundleId") != self._bundle_id:
            raise UnverifiedPurchaseError("notification is for another app")
        if not self._accepts_environment(data.get("environment")):
            raise UnverifiedPurchaseError("notification environment not accepted")
        # Apple includes appAppleId only on Production notifications.
        if (
            data.get("environment") == "Production"
            and data.get("appAppleId") != self._app_apple_id
        ):
            raise UnverifiedPurchaseError("notification is for another App Store app")
        signed_transaction = data.get("signedTransactionInfo")
        if not isinstance(signed_transaction, str) or not signed_transaction:
            raise UnverifiedPurchaseError("notification carried no transaction")
        purchase = self._purchase(self._verify_signed(signed_transaction))
        return AppleNotification(
            notification_type=str(claims.get("notificationType") or ""),
            subtype=str(claims.get("subtype") or ""),
            purchase=purchase,
        )
