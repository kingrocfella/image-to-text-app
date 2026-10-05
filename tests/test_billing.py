"""ScanGenAI Pro on a real PostgreSQL: how a subscription row may change, and
who counts as Pro. Ported from NoAlibi (and, before it, Lost Vowels)."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.billing import service
from app.services.billing.entitlement import get_entitlement
from app.services.billing.types import VerifiedPurchase
from tests.pg import fresh_schema, requires_postgres

NOW = datetime.now(timezone.utc)
YEARLY = "scangenai_pro_yearly"


def _purchase(expires: datetime | None, *, bought=NOW, revoked=False, auth=False):
    return VerifiedPurchase("sub-1", YEARLY, bought, expires, revoked, auth)


async def _new_user(conn, *, pro_until=None) -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO users (id, name, email, is_verified, pro_until) "
            "VALUES (:id, 'U', :email, true, :pro_until)"
        ),
        {"id": user_id, "email": f"{user_id}@example.com", "pro_until": pro_until},
    )
    return user_id


@requires_postgres
class SubscriptionRuleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = await fresh_schema()
        self.sessions = async_sessionmaker(self.engine)
        async with self.engine.begin() as conn:
            self.user = await _new_user(conn)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _record(self, purchase, owner="user") -> bool:
        async with self.sessions() as db:
            if owner == "user":
                return await service._record(db, self.user, "ios", purchase)
            return await service.apply_store_update(db, "ios", purchase)

    async def _pro(self) -> bool:
        async with self.sessions() as db:
            return (await get_entitlement(db, self.user)).pro

    async def test_a_replayed_old_receipt_cannot_shorten_a_subscription(self) -> None:
        self.assertTrue(await self._record(_purchase(NOW + timedelta(days=365))))
        await self._record(_purchase(NOW + timedelta(days=30)), owner=None)
        async with self.sessions() as db:
            expires = (
                await db.execute(text("SELECT expires_at FROM purchases"))
            ).scalar_one()
        self.assertGreater(expires, NOW + timedelta(days=300))

    async def test_an_authoritative_answer_can_end_access(self) -> None:
        await self._record(_purchase(NOW + timedelta(days=365)))
        await self._record(_purchase(NOW - timedelta(hours=1), auth=True), owner=None)
        self.assertFalse(await self._pro())

    async def test_refund_holds_against_replay_until_a_new_purchase(self) -> None:
        await self._record(
            _purchase(NOW + timedelta(days=30), bought=NOW - timedelta(days=1))
        )
        await self._record(_purchase(None, revoked=True), owner=None)
        self.assertFalse(await self._pro())
        # Replaying the refunded period's receipt must not bring Pro back.
        await self._record(
            _purchase(NOW + timedelta(days=30), bought=NOW - timedelta(days=1))
        )
        self.assertFalse(await self._pro())
        # A period bought after the refund does. (Measured from now, not from
        # when this module was imported: the refund above is stamped now.)
        later = datetime.now(timezone.utc) + timedelta(minutes=1)
        await self._record(_purchase(later + timedelta(days=30), bought=later))
        self.assertTrue(await self._pro())

    async def test_store_updates_never_change_the_owner(self) -> None:
        await self._record(_purchase(NOW + timedelta(days=30)))
        await self._record(_purchase(NOW + timedelta(days=60)), owner=None)
        async with self.sessions() as db:
            owner = (
                await db.execute(text("SELECT user_id FROM purchases"))
            ).scalar_one()
        self.assertEqual(owner, self.user)

    async def test_grant_expired_grant_and_nothing(self) -> None:
        async with self.engine.begin() as conn:
            granted = await _new_user(conn, pro_until=NOW + timedelta(days=90))
            lapsed = await _new_user(conn, pro_until=NOW - timedelta(days=1))
        async with self.sessions() as db:
            grant = await get_entitlement(db, granted)
            self.assertEqual((grant.pro, grant.source), (True, "grant"))
            self.assertFalse((await get_entitlement(db, lapsed)).pro)
            self.assertFalse((await get_entitlement(db, self.user)).pro)

    async def test_a_subscription_for_another_product_is_not_pro(self) -> None:
        other = VerifiedPurchase(
            "sub-2", "noalibi_pro_yearly", NOW, NOW + timedelta(days=30), False, False
        )
        async with self.sessions() as db:
            with self.assertRaises(service.WrongProductError):
                await service.redeem_purchase(
                    db, _Fixed(other), self.user, "ios", "receipt"
                )
        self.assertFalse(await self._pro())

    async def test_deleting_the_account_deletes_its_purchases(self) -> None:
        await self._record(_purchase(NOW + timedelta(days=30)))
        async with self.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": self.user}
            )
            left = (
                await conn.execute(text("SELECT count(*) FROM purchases"))
            ).scalar_one()
        self.assertEqual(left, 0)


class _Fixed:
    def __init__(self, purchase: VerifiedPurchase) -> None:
        self._purchase = purchase

    async def verify(self, platform, receipt) -> VerifiedPurchase:
        return self._purchase


if __name__ == "__main__":
    unittest.main()
