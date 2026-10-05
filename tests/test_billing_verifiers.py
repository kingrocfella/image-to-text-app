"""App Store and Play verification, and the free-tier report redaction.

The Apple tests sign with a generated chain that carries Apple's marker
extensions, so every rejection path is exercised without real receipts.
"""

import base64
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID, ObjectIdentifier

from app.services.billing.apple import AppleStoreKitVerifier
from app.services.billing.google import GooglePlayBillingClient
from app.services.billing.types import UnverifiedPurchaseError

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
LEAF_OID = ObjectIdentifier("1.2.840.113635.100.6.11.1")
INTERMEDIATE_OID = ObjectIdentifier("1.2.840.113635.100.6.2.1")
BUNDLE = "com.leonfrontier.scangenai"


def _cert(name, key, issuer_name, issuer_key, *, ca, marker=None):
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if marker is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(marker, b"\x05\x00"), critical=False
        )
    return builder.sign(issuer_key, hashes.SHA256())


class Chain:
    def __init__(self, *, leaf_marker=LEAF_OID) -> None:
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.int_key = ec.generate_private_key(ec.SECP256R1())
        self.leaf_key = ec.generate_private_key(ec.SECP256R1())
        self.root = _cert("Root", self.root_key, "Root", self.root_key, ca=True)
        self.intermediate = _cert(
            "WWDR",
            self.int_key,
            "Root",
            self.root_key,
            ca=True,
            marker=INTERMEDIATE_OID,
        )
        self.leaf = _cert(
            "Leaf", self.leaf_key, "WWDR", self.int_key, ca=False, marker=leaf_marker
        )

    def sign(self, claims: dict, *, key=None) -> str:
        x5c = [
            base64.b64encode(c.public_bytes(Encoding.DER)).decode()
            for c in (self.leaf, self.intermediate, self.root)
        ]
        return jwt.encode(
            claims, key or self.leaf_key, algorithm="ES256", headers={"x5c": x5c}
        )

    def verifier(self, environment: str = "Production") -> AppleStoreKitVerifier:
        return AppleStoreKitVerifier(
            root_pem=self.root.public_bytes(Encoding.PEM),
            bundle_id=BUNDLE,
            environment=environment,
            app_apple_id=123,
            now=lambda: NOW,
        )


def _transaction(**overrides) -> dict:
    claims = {
        "bundleId": BUNDLE,
        "environment": "Production",
        "productId": "scangenai_pro_yearly",
        "transactionId": "2000",
        "originalTransactionId": "1000",
        "purchaseDate": int((NOW - timedelta(days=1)).timestamp() * 1000),
        "expiresDate": int((NOW + timedelta(days=364)).timestamp() * 1000),
    }
    claims.update(overrides)
    return claims


class AppleVerifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.chain = Chain()
        self.verifier = self.chain.verifier()

    async def test_accepts_a_genuine_transaction_keyed_by_original_id(self) -> None:
        purchase = await self.verifier.verify_transaction(
            self.chain.sign(_transaction())
        )
        self.assertEqual(purchase.transaction_id, "1000")
        self.assertEqual(purchase.product_id, "scangenai_pro_yearly")
        self.assertFalse(purchase.revoked)
        self.assertFalse(purchase.authoritative)  # a replayable receipt

    async def test_rejects_forgeries_and_foreign_data(self) -> None:
        other_root = Chain()
        cases = {
            "chain from another root": other_root.sign(_transaction()),
            "no leaf marker": Chain(leaf_marker=None).sign(_transaction()),
            "another app": self.chain.sign(_transaction(bundleId="com.evil.app")),
            "local Xcode testing": self.chain.sign(_transaction(environment="Xcode")),
            "signed by the wrong key": self.chain.sign(
                _transaction(), key=ec.generate_private_key(ec.SECP256R1())
            ),
            "not a JWS": "not-a-jws",
        }
        for name, jws in cases.items():
            with self.subTest(name), self.assertRaises(UnverifiedPurchaseError):
                await self.verifier.verify_transaction(jws)

    async def test_production_accepts_sandbox_but_sandbox_refuses_production(
        self,
    ) -> None:
        sandbox_receipt = self.chain.sign(_transaction(environment="Sandbox"))
        await self.verifier.verify_transaction(sandbox_receipt)
        with self.assertRaises(UnverifiedPurchaseError):
            await self.chain.verifier("Sandbox").verify_transaction(
                self.chain.sign(_transaction())
            )

    async def test_refund_and_upgrade(self) -> None:
        refunded = await self.verifier.verify_transaction(
            self.chain.sign(_transaction(revocationDate=int(NOW.timestamp() * 1000)))
        )
        self.assertTrue(refunded.revoked)
        upgraded = await self.verifier.verify_transaction(
            self.chain.sign(_transaction(isUpgraded=True))
        )
        self.assertIsNone(upgraded.expires_at)  # neither revokes nor extends
        self.assertFalse(upgraded.revoked)

    async def test_notification_checks_app_id_on_production(self) -> None:
        inner = self.chain.sign(_transaction())

        def notification(**data) -> str:
            body = {"bundleId": BUNDLE, "environment": "Production", "appAppleId": 123}
            body.update(data, signedTransactionInfo=inner)
            return self.chain.sign({"notificationType": "DID_RENEW", "data": body})

        parsed = await self.verifier.verify_notification(notification())
        self.assertEqual(parsed.notification_type, "DID_RENEW")
        with self.assertRaises(UnverifiedPurchaseError):
            await self.verifier.verify_notification(notification(appAppleId=999))


def _play(status: int, body: dict | None = None) -> MagicMock:
    response = MagicMock(status_code=status, ok=200 <= status < 300)
    response.json.return_value = body or {}
    return response


class GooglePlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GooglePlayBillingClient.__new__(GooglePlayBillingClient)
        self.client._app_path = "/androidpublisher/v3/applications/pkg"
        self.client._now = lambda: NOW
        self.client._session = MagicMock()

    def _answer(self, response: MagicMock) -> None:
        self.client._session.get.return_value = response

    def test_active_subscription_is_authoritative_and_takes_latest_line_item(
        self,
    ) -> None:
        self._answer(
            _play(
                200,
                {
                    "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
                    "startTime": "2026-09-01T00:00:00Z",
                    "lineItems": [
                        {
                            "productId": "scangenai_pro_monthly",
                            "expiryTime": "2026-10-01T00:00:00Z",
                        },
                        {
                            "productId": "scangenai_pro_yearly",
                            "expiryTime": "2027-09-01T00:00:00Z",
                        },
                    ],
                },
            )
        )
        purchase = self.client._verify_sync("token-1")
        self.assertEqual(purchase.product_id, "scangenai_pro_yearly")
        self.assertEqual(purchase.transaction_id, "token-1")
        self.assertTrue(purchase.authoritative)

    def test_grace_period_extends_past_now(self) -> None:
        self._answer(
            _play(
                200,
                {
                    "subscriptionState": "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
                    "lineItems": [
                        {
                            "productId": "scangenai_pro_monthly",
                            "expiryTime": "2026-09-28T00:00:00Z",
                        }
                    ],
                },
            )
        )
        self.assertGreater(self.client._verify_sync("t").expires_at, NOW)

    def test_pending_and_unknown_tokens_are_not_granted(self) -> None:
        for response in (
            _play(200, {"subscriptionState": "SUBSCRIPTION_STATE_PENDING"}),
            _play(404),
        ):
            with self.subTest(response.status_code):
                self._answer(response)
                with self.assertRaises(UnverifiedPurchaseError):
                    self.client._verify_sync("t")


if __name__ == "__main__":
    unittest.main()
