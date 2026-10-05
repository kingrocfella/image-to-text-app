"""Operations snapshot, alert rules, the monitor's mail cycle, and migrations."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import JobRun, User
from app.database.postgres import init_db
from app.lifespan import purge_expired_security_rows
from app.services.ops import Snapshot, evaluate, take_snapshot
from app.services.ops_monitor import run_ops_check
from app.utils import get_password_hash
from tests.conftest import TestSessionLocal, requires_postgres, test_engine

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _subjects(snapshot: Snapshot) -> set[str]:
    return {alert.subject for alert in evaluate(snapshot, get_settings())}


def test_no_backup_ever_is_critical():
    alerts = evaluate(Snapshot(taken_at=NOW), get_settings())
    assert [(a.severity, a.subject) for a in alerts] == [("critical", "backups")]


def test_a_fresh_backup_and_quiet_queue_raise_nothing():
    snapshot = Snapshot(taken_at=NOW, last_backup_at=NOW - timedelta(hours=3))
    assert evaluate(snapshot, get_settings()) == []


def test_a_stale_backup_is_critical():
    snapshot = Snapshot(taken_at=NOW, last_backup_at=NOW - timedelta(hours=49))
    assert _subjects(snapshot) == {"backups"}


def test_stuck_and_failing_jobs_alert():
    fresh = NOW - timedelta(hours=1)
    assert _subjects(Snapshot(taken_at=NOW, last_backup_at=fresh, jobs_stuck=2)) == {
        "jobs-stuck"
    }
    assert _subjects(
        Snapshot(taken_at=NOW, last_backup_at=fresh, jobs_24h=200, jobs_failed_24h=10)
    ) == {"job-failures"}
    # A high failure *rate* alerts before the absolute count does...
    assert _subjects(
        Snapshot(taken_at=NOW, last_backup_at=fresh, jobs_24h=20, jobs_failed_24h=5)
    ) == {"job-failures"}
    # ...but not on a handful of jobs, where one failure is 50%.
    assert (
        _subjects(
            Snapshot(taken_at=NOW, last_backup_at=fresh, jobs_24h=4, jobs_failed_24h=2)
        )
        == set()
    )


async def _seed(db: AsyncSession) -> User:
    user = User(
        name="Ada",
        email="ada@example.com",
        hashed_password=get_password_hash("password123"),
        is_verified=True,
    )
    db.add(user)
    await db.commit()
    return user


@requires_postgres
@pytest.mark.asyncio
async def test_snapshot_measures_the_real_tables(db_session: AsyncSession):
    user = await _seed(db_session)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            JobRun(
                message_id="ok", user_id=user.id, job_type="image", status="finished"
            ),
            JobRun(message_id="bad", user_id=user.id, job_type="rag", status="failed"),
            JobRun(
                message_id="stuck",
                user_id=user.id,
                job_type="sound",
                status="queued",
                created_at=now - timedelta(hours=2),
            ),
        ]
    )
    await db_session.execute(
        text("INSERT INTO backup_runs (filename, size_bytes) VALUES ('a.dump', 2048)")
    )
    await db_session.commit()

    snapshot = await take_snapshot(db_session, get_settings())

    assert snapshot.users_total == snapshot.users_verified == 1
    assert snapshot.jobs_24h == 3
    assert snapshot.jobs_failed_24h == 1
    assert snapshot.jobs_stuck == 1
    assert snapshot.last_backup_bytes == 2048
    assert {a.subject for a in snapshot.alerts} == {"jobs-stuck"}


@requires_postgres
@pytest.mark.asyncio
async def test_monitor_mails_once_repeats_later_and_reports_resolution(
    db_session: AsyncSession,
):
    with patch(
        "app.services.ops_monitor.send_operator_email", new_callable=AsyncMock
    ) as mail:
        mail.return_value = True
        start = datetime.now(timezone.utc)

        await run_ops_check(TestSessionLocal, now=start)
        assert [call.args[0] for call in mail.call_args_list] == ["critical: backups"]

        # Still broken an hour later: no second email inside the repeat window.
        await run_ops_check(TestSessionLocal, now=start + timedelta(hours=1))
        assert mail.call_count == 1

        # Still broken a day later: reminded.
        await run_ops_check(TestSessionLocal, now=start + timedelta(hours=25))
        assert mail.call_count == 2

        await db_session.execute(
            text(
                "INSERT INTO backup_runs (filename, size_bytes, finished_at) "
                "VALUES ('a.dump', 10, :at)"
            ),
            {"at": start + timedelta(hours=25)},
        )
        await db_session.commit()
        await run_ops_check(TestSessionLocal, now=start + timedelta(hours=26))
        assert mail.call_args_list[-1].args[0] == "resolved: backups"


@requires_postgres
@pytest.mark.asyncio
async def test_migrations_upgrade_a_database_created_before_they_existed(
    db_session: AsyncSession,
):
    """The production database was built by create_all with the old models."""
    async with test_engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("""
            CREATE TABLE users (
                id UUID PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                hashed_password VARCHAR(255) NOT NULL,
                is_verified BOOLEAN NOT NULL DEFAULT false,
                verification_token VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""))
        await conn.execute(text("""
            INSERT INTO users (id, name, email, hashed_password, is_verified)
            VALUES (gen_random_uuid(), 'Old', 'old@example.com', 'x', true)"""))

    await init_db(test_engine)
    await init_db(test_engine)  # idempotent: a restart applies nothing twice

    async with test_engine.connect() as conn:
        columns = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'users'"
                    )
                )
            ).scalars()
        )
        applied = (
            await conn.execute(text("SELECT count(*) FROM schema_migrations"))
        ).scalar_one()
        kept = (await conn.execute(text("SELECT count(*) FROM users"))).scalar_one()
        tables = set(
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).scalars()
        )

    assert {
        "verification_expires_at",
        "password_reset_token",
        "password_reset_expires_at",
        "password_changed_at",
        "pro_until",
        "google_sub",
        "apple_sub",
    } <= columns
    assert applied == 4
    assert kept == 1
    assert {
        "rate_limit_buckets",
        "backup_runs",
        "admin_security",
        "ops_alert_state",
        "usage_counters",
        "job_runs",
        "refresh_sessions",
        "purchases",
    } <= tables


@requires_postgres
@pytest.mark.asyncio
async def test_expired_security_rows_are_purged(db_session: AsyncSession, monkeypatch):
    from app import lifespan
    from app.database import RefreshSession, TokenBlacklist

    user = await _seed(db_session)
    now = datetime.now(timezone.utc)
    import uuid

    db_session.add_all(
        [
            TokenBlacklist(
                token="old", user_id=user.id, expires_at=now - timedelta(hours=1)
            ),
            TokenBlacklist(
                token="live", user_id=user.id, expires_at=now + timedelta(hours=1)
            ),
            RefreshSession(
                token_hash="a" * 64,
                family_id=uuid.uuid4(),
                user_id=user.id,
                expires_at=now - timedelta(days=1),
            ),
            RefreshSession(
                token_hash="b" * 64,
                family_id=uuid.uuid4(),
                user_id=user.id,
                expires_at=now + timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(lifespan, "AsyncSessionLocal", TestSessionLocal)

    removed = await purge_expired_security_rows()

    assert removed == 2
    remaining = (
        (await db_session.execute(text("SELECT token FROM token_blacklist")))
        .scalars()
        .all()
    )
    assert remaining == ["live"]
