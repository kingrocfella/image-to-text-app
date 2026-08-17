"""Authentication routes."""

import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from app.schemas import (
    DeleteAccountRequest,
    MessageResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
)
from app.utils import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_verification_token,
    get_password_hash,
    token_fingerprint,
    verify_password,
)
from app.utils.logger import logger
from app.database import RefreshSession, TokenBlacklist, User, get_db
from app.dependencies import get_current_user
from app.queues import mark_account_deleted_and_purge_jobs
from app.utils.rag_vectorstore import delete_user_pdf_data

load_dotenv()

router = APIRouter(prefix="/auth", tags=["authentication"])

security = HTTPBearer()


def send_verification_email(email: str, verification_token: str):
    """Send verification email to user."""

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port_str = os.getenv("SMTP_PORT")
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    app_url = os.getenv("APP_URL")

    if not smtp_server:
        raise ValueError("SMTP_SERVER environment variable is not set")
    if not smtp_port_str:
        raise ValueError("SMTP_PORT environment variable is not set")
    if not smtp_username:
        raise ValueError("SMTP_USERNAME environment variable is not set")
    if not smtp_password:
        raise ValueError("SMTP_PASSWORD environment variable is not set")
    if not app_url:
        raise ValueError("APP_URL environment variable is not set")

    smtp_port = int(smtp_port_str)

    try:
        verification_url = f"{app_url}/auth/verify-email?token={verification_token}"
        msg = MIMEMultipart()
        msg["From"] = smtp_username
        msg["To"] = email
        msg["Subject"] = "Verify Your Email - ScanGenAI API"

        body = f"""
        Please verify your email by clicking the link below:
        {verification_url}

        """
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to send verification email: %s", type(exc).__name__, exc_info=True
        )


@router.post(
    "/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED
)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user"""
    try:
        logger.info("Registration attempt")

        # Check if user already exists
        stmt = select(User).where(User.email == user_data.email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()
        if existing_user:
            logger.info("Registration request matched an existing account")
            return MessageResponse(
                message="If the address can be registered, a verification email will be sent."
            )

        # Create new user
        verification_token = generate_verification_token()
        hashed_password = get_password_hash(user_data.password)

        new_user = User(
            name=user_data.name,
            email=user_data.email,
            hashed_password=hashed_password,
            is_verified=False,
            verification_token=token_fingerprint(verification_token),
        )

        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # Send verification email
        send_verification_email(user_data.email, verification_token)

        logger.info("User registered successfully (ID: %s)", new_user.id)

        # Return the same generic message as the already-registered branch so the
        # response body cannot be used to enumerate which emails have accounts.
        return MessageResponse(
            message="If the address can be registered, a verification email will be sent."
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Registration error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed",
        ) from exc


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login user and return access and refresh tokens."""
    try:
        logger.info("Login attempt")

        # Find user by email
        stmt = select(User).where(User.email == credentials.email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning("Login failed: invalid credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        # Check if user has verified their email
        if not user.is_verified:  # type: ignore[attr-defined]
            logger.warning("Login failed: invalid credentials or account state")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        hashed_password = user.hashed_password

        if hashed_password is None:
            logger.warning("Login failed: invalid credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        # Verify password
        if not verify_password(credentials.password, str(hashed_password)):  # type: ignore[arg-type]
            logger.warning("Login failed: invalid credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        # Create tokens
        user_id = str(user.id)
        access_token = create_access_token(data={"sub": user_id})
        family_id = uuid4()
        user_refresh_token = create_refresh_token(
            data={"sub": user_id, "family": str(family_id)}
        )
        refresh_payload = decode_token(user_refresh_token)
        if refresh_payload is None:
            raise RuntimeError("Newly issued refresh token could not be decoded")
        db.add(
            RefreshSession(
                token_hash=token_fingerprint(user_refresh_token),
                family_id=family_id,
                user_id=user.id,
                expires_at=datetime.fromtimestamp(
                    refresh_payload["exp"], tz=timezone.utc
                ),
            )
        )
        await db.commit()

        logger.info("Login successful (ID: %s)", user.id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=user_refresh_token,
            name=str(user.name),  # type: ignore[arg-type]
            user_id=str(user.id),
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Login error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed",
        ) from exc


@router.get(
    "/verify-email", response_model=MessageResponse, status_code=status.HTTP_200_OK
)
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    """Verify user email with verification token from query parameter."""
    try:
        logger.info("Email verification attempt")

        stmt = select(User).where(User.verification_token == token_fingerprint(token))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning("Email verification failed: invalid token")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification token",
            )

        if bool(user.is_verified):
            logger.info("Email already verified (ID: %s)", user.id)
            return MessageResponse(message="Email already verified")

        # Update user as verified
        user.is_verified = True  # type: ignore[assignment]
        user.verification_token = None  # type: ignore[assignment]
        user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        await db.commit()

        logger.info("Email verified successfully (ID: %s)", user.id)

        return MessageResponse(message="Email verified successfully")
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Email verification error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email verification failed",
        ) from exc


@router.post("/refresh", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def refresh_token(
    request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)
):
    """Refresh access token using refresh token. Requires valid refresh token."""
    try:
        token = request.refresh_token
        logger.info("Token refresh attempt")

        # Decode refresh token
        payload = decode_token(token)
        if payload is None:
            logger.warning("Token refresh failed: Invalid or expired token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        # Check token type
        if payload.get("type") != "refresh":
            logger.warning("Token refresh failed: Invalid token type")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )

        # Bind the signed token to its server-side, one-time session.
        user_id_str = payload.get("sub")
        family_id_str = payload.get("family")
        if not user_id_str or not family_id_str:
            logger.warning("Token refresh failed: Missing user ID in token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        try:
            user_id = UUID(user_id_str)
            family_id = UUID(family_id_str)
        except (TypeError, ValueError) as exc:
            logger.warning("Token refresh failed: invalid token identifiers")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            ) from exc

        current_hash = token_fingerprint(token)
        stmt = (
            select(RefreshSession)
            .where(RefreshSession.token_hash == current_hash)
            .with_for_update()
        )
        result = await db.execute(stmt)
        refresh_session = result.scalar_one_or_none()
        if (
            refresh_session is None
            or refresh_session.revoked_at is not None
            or refresh_session.user_id != user_id
            or refresh_session.family_id != family_id
        ):
            await db.execute(
                update(RefreshSession)
                .where(RefreshSession.family_id == family_id)
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await db.commit()
            logger.warning("Token refresh reuse or unknown session detected")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked",
            )

        # Verify user exists
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            logger.warning("Token refresh failed: User not found - %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        # Rotate the refresh token. The database row lock makes concurrent reuse lose.
        access_token = create_access_token(data={"sub": user_id_str})
        next_refresh_token = create_refresh_token(
            data={"sub": user_id_str, "family": str(family_id)}
        )
        next_payload = decode_token(next_refresh_token)
        if next_payload is None:
            raise RuntimeError("Newly issued refresh token could not be decoded")
        next_hash = token_fingerprint(next_refresh_token)
        refresh_session.revoked_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        refresh_session.replaced_by_hash = next_hash  # type: ignore[assignment]
        db.add(
            RefreshSession(
                token_hash=next_hash,
                family_id=family_id,
                user_id=user_id,
                expires_at=datetime.fromtimestamp(next_payload["exp"], tz=timezone.utc),
            )
        )
        await db.commit()

        logger.info("Token refreshed successfully for user: %s", user_id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=next_refresh_token,
            name=str(user.name),  # type: ignore[arg-type]
            user_id=str(user.id),
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Token refresh error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed",
        ) from exc


@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def logout(
    request: RefreshTokenRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Logout user by blacklisting access and refresh tokens."""
    try:
        logger.info("Logout attempt (ID: %s)", current_user.id)

        access_token = credentials.credentials
        user_refresh_token = request.refresh_token

        # Blacklist access token
        if access_token:
            payload = decode_token(access_token)
            if payload:
                exp_timestamp = payload.get("exp")
                if exp_timestamp:
                    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                else:
                    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

                blacklist_entry = TokenBlacklist(
                    token=token_fingerprint(access_token),
                    user_id=current_user.id,
                    expires_at=expires_at,
                )
                db.add(blacklist_entry)

        # Revoke the whole refresh family so all devices in this session are invalid.
        if user_refresh_token:
            payload = decode_token(user_refresh_token)
            if payload and payload.get("sub") == str(current_user.id):
                try:
                    family_id = UUID(payload["family"])
                except (KeyError, TypeError, ValueError):
                    family_id = None
                if family_id is not None:
                    await db.execute(
                        update(RefreshSession)
                        .where(
                            RefreshSession.family_id == family_id,
                            RefreshSession.user_id == current_user.id,
                        )
                        .values(revoked_at=datetime.now(timezone.utc))
                    )

        await db.commit()

        logger.info(
            "User logged out successfully (ID: %s)",
            current_user.id,
        )

        return MessageResponse(message="Logged out successfully. Tokens revoked.")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Logout error for user %s: %s", current_user.id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logout failed",
        ) from exc


@router.delete(
    "/account", response_model=MessageResponse, status_code=status.HTTP_200_OK
)
async def delete_account(
    request: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Permanently erase the authenticated account and its stored document data."""
    result = await db.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    )
    locked_user = result.scalar_one_or_none()
    if locked_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if not verify_password(request.password, str(locked_user.hashed_password)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    try:
        await delete_user_pdf_data(locked_user.id, db)
        mark_account_deleted_and_purge_jobs(str(locked_user.id))
        await db.execute(
            delete(TokenBlacklist).where(TokenBlacklist.user_id == locked_user.id)
        )
        await db.execute(
            delete(RefreshSession).where(RefreshSession.user_id == locked_user.id)
        )
        await db.delete(locked_user)
        await db.commit()
        logger.info("Account permanently deleted (ID: %s)", locked_user.id)
        return MessageResponse(message="Account and stored data permanently deleted")
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        await db.rollback()
        logger.error(
            "Account deletion failed (ID: %s): %s",
            locked_user.id,
            type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account deletion could not be completed",
        ) from exc
