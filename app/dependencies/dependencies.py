"""FastAPI dependencies for authentication."""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils import decode_token, token_fingerprint
from app.utils.logger import logger
from app.database import TokenBlacklist, User, get_db

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user from JWT token."""
    token = credentials.credentials

    try:
        # Check if token is blacklisted
        stmt = select(TokenBlacklist).where(
            TokenBlacklist.token == token_fingerprint(token)
        )
        result = await db.execute(stmt)
        blacklisted = result.scalar_one_or_none()
        if blacklisted:
            logger.warning("Authentication failed: Token blacklisted")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Decode token
        payload = decode_token(token)
        if payload is None:
            logger.warning("Authentication failed: Invalid or expired token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check token type
        if payload.get("type") != "access":
            logger.warning("Authentication failed: Invalid token type")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Get user from database
        user_id_str = payload.get("sub")
        if not user_id_str:
            logger.warning("Authentication failed: Missing user ID in token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            user_id = UUID(user_id_str)
        except ValueError as exc:
            logger.warning(
                "Authentication failed: Invalid user ID format - %s", user_id_str
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user ID format",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            logger.warning("Authentication failed: User not found - %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.debug("User authenticated (ID: %s)", user.id)
        return user
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Authentication error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current active (verified) user."""
    if not bool(current_user.is_verified):  # type: ignore[arg-type]
        logger.warning("Access denied: email not verified (ID: %s)", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified",
        )
    return current_user
