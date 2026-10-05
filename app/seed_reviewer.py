"""Create or reset the store reviewer account.

Apple App Review and Google Play review both need a working sign-in. This
makes one ordinary, already-verified account (no special rights) and can be
re-run to reset its password:

    make seed-reviewer-account EMAIL=review@example.com

The password is read from the terminal, never from the command line (where it
would land in shell history and the process list) and never from this file.
"""

import asyncio
import getpass
import sys
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database import AsyncSessionLocal, RefreshSession, User
from app.schemas.auth_schemas import PASSWORD_MAX_BYTES, PASSWORD_MIN_LENGTH
from app.utils import get_password_hash

NAME = "Store Review"


async def seed(email: str, password: str) -> str:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            session.add(
                User(
                    name=NAME,
                    email=email,
                    hashed_password=get_password_hash(password),
                    is_verified=True,
                )
            )
            await session.commit()
            return "created"
        user.hashed_password = get_password_hash(password)
        user.is_verified = True
        user.password_changed_at = now
        await session.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == user.id)
            .values(revoked_at=now)
        )
        await session.commit()
        return "password reset"


def main() -> int:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print("usage: python -m app.seed_reviewer <email>", file=sys.stderr)
        return 64
    password = getpass.getpass("Reviewer password: ")
    if len(password) < PASSWORD_MIN_LENGTH:
        print(f"Use at least {PASSWORD_MIN_LENGTH} characters.", file=sys.stderr)
        return 65
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        print("That password is too long.", file=sys.stderr)
        return 65
    if getpass.getpass("Again: ") != password:
        print("The two passwords do not match.", file=sys.stderr)
        return 65
    outcome = asyncio.run(seed(sys.argv[1].strip().lower(), password))
    print(f"Reviewer account {outcome}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
