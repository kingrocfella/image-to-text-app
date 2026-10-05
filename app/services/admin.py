"""Admin console authentication (modelled on Letterbolt and Lost Vowels).

- Off unless ADMIN_DASHBOARD_TOKEN is set; the routes are not even registered.
- Username and token are both compared in constant time, always both.
- Failed attempts are counted in PostgreSQL (``admin_security``), so the
  lockout holds across API workers and restarts. A locked console refuses
  before comparing and records nothing, so polling cannot extend the lock.
- Sessions are stateless signed cookies that expire after
  ADMIN_AUTO_LOGOUT_MINUTES idle. The signing key is derived from SECRET_KEY
  and the admin token, so rotating either signs every console session out.
"""

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.postgres import AsyncSessionLocal
from app.utils.logger import logger

SESSION_COOKIE = "scangenai_admin"


class AdminDenied(Exception):
    """Wrong username or token."""


class AdminLocked(Exception):
    """Too many failures; the console is shut until the lock expires."""


_LOCK_ROW = text(
    "SELECT failed_attempts, locked_until FROM admin_security WHERE id = 1 FOR UPDATE"
)
_RECORD_FAILURE = text("""
    UPDATE admin_security SET
      failed_attempts = CASE WHEN failed_attempts + 1 >= :threshold THEN 0
                             ELSE failed_attempts + 1 END,
      locked_until = CASE WHEN failed_attempts + 1 >= :threshold
                          THEN :lock_until
                          ELSE locked_until END,
      last_failed_at = :now
    WHERE id = 1
    RETURNING locked_until
    """)
_RESET = text(
    "UPDATE admin_security SET failed_attempts = 0, locked_until = NULL WHERE id = 1"
)


def _equal(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented.encode(), expected.encode())


class AdminAuth:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._signing_key = hmac.new(
            settings.secret_key.encode(),
            b"admin-session:" + settings.admin_dashboard_token.encode(),
            hashlib.sha256,
        ).digest()

    async def authorize(self, username: str, token: str) -> None:
        """Raise AdminLocked or AdminDenied, or return on success."""
        s = self._settings
        now = datetime.now(timezone.utc)
        # Evaluate both comparisons so timing reveals neither half.
        user_ok = _equal(username, s.admin_dashboard_username)
        token_ok = _equal(token, s.admin_dashboard_token)
        async with self._session_factory() as session:
            row = (await session.execute(_LOCK_ROW)).one()
            if row.locked_until is not None and row.locked_until > now:
                await session.rollback()
                raise AdminLocked
            if user_ok and token_ok:
                await session.execute(_RESET)
                await session.commit()
                return
            locked_until = (
                await session.execute(
                    _RECORD_FAILURE,
                    {
                        "threshold": s.admin_lock_threshold,
                        "lock_until": now + timedelta(minutes=s.admin_lock_minutes),
                        "now": now,
                    },
                )
            ).scalar_one()
            await session.commit()
        if locked_until is not None and locked_until > now:
            logger.warning("Admin console locked after repeated failed sign-ins")
        raise AdminDenied

    def _signature(self, expires: int) -> str:
        return hmac.new(
            self._signing_key, f"admin:{expires}".encode(), hashlib.sha256
        ).hexdigest()

    def issue_session(self, now: float | None = None) -> str:
        idle = timedelta(minutes=self._settings.admin_auto_logout_minutes)
        expires = int((now or time.time()) + idle.total_seconds())
        return f"{expires}.{self._signature(expires)}"

    def valid_session(self, cookie: str | None, now: float | None = None) -> bool:
        if not cookie or "." not in cookie:
            return False
        expires_text, signature = cookie.split(".", 1)
        if not expires_text.isdigit():
            return False
        expires = int(expires_text)
        return expires > (now or time.time()) and hmac.compare_digest(
            signature, self._signature(expires)
        )
