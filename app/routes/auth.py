"""Authentication routes.

``router`` is the JSON API the app calls (served under ``/v1``). ``web_router``
holds the pages a browser opens from an email link; delivered emails point at
those, so they are never versioned.
"""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import RefreshSession, TokenBlacklist, User, get_db
from app.dependencies import get_current_user
from app.paths import Api, Web
from app.queues import mark_account_deleted_and_purge_jobs
from app.schemas import (
    AppleLoginRequest,
    DeleteAccountRequest,
    EmailRequest,
    GoogleLoginRequest,
    MessageResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
)
from app.schemas.auth_schemas import PASSWORD_MAX_BYTES, PASSWORD_MIN_LENGTH
from app.services.apple_auth import verify_apple_identity_token
from app.utils import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_verification_token,
    get_password_hash,
    token_fingerprint,
    verify_password,
)
from app.utils.email_utils import (
    render_template,
    send_password_reset_email,
    send_verification_email,
)
from app.utils.google_oauth import GoogleOAuthConfigError, verify_google_id_token
from app.utils.logger import logger
from app.utils.rag_vectorstore import delete_user_pdf_data
from app.utils.rate_limit import limiter

router = APIRouter(tags=["authentication"])
web_router = APIRouter(tags=["authentication pages"], include_in_schema=False)

security = HTTPBearer()

# One answer for "sent", "no such account" and "already verified", so the
# response cannot be used to learn which emails have accounts.
REGISTER_MESSAGE = (
    "If the address can be registered, a verification email will be sent."
)
RESEND_MESSAGE = (
    "If that address has an unverified account, a new verification email is on its way."
)
FORGOT_MESSAGE = "If that address has an account, a password reset email is on its way."

# Verified against when the account does not exist, so an unknown email costs
# the same bcrypt work as a wrong password and timing reveals nothing.
_DUMMY_HASH = get_password_hash(secrets.token_urlsafe(16))


def _verification_deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        hours=get_settings().verification_token_expire_hours
    )


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes in tests; PostgreSQL returns aware ones."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.post(
    Api.AUTH_REGISTER,
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/hour")
async def register(
    request: Request,  # pylint: disable=unused-argument
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user"""
    try:
        logger.info("Registration attempt")

        # Check if user already exists
        stmt = select(User).where(User.email == user_data.email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()
        if existing_user:
            logger.info("Registration request matched an existing account")
            return MessageResponse(message=REGISTER_MESSAGE)

        # Create new user
        verification_token = generate_verification_token()
        hashed_password = get_password_hash(user_data.password)

        new_user = User(
            name=user_data.name,
            email=user_data.email,
            hashed_password=hashed_password,
            is_verified=False,
            verification_token=token_fingerprint(verification_token),
            verification_expires_at=_verification_deadline(),
        )

        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # SMTP is blocking; keep it off the event loop.
        await asyncio.to_thread(
            send_verification_email,
            user_data.email,
            verification_token,
            name=user_data.name,
        )

        logger.info("User registered successfully (ID: %s)", new_user.id)

        # Return the same generic message as the already-registered branch so the
        # response body cannot be used to enumerate which emails have accounts.
        return MessageResponse(message=REGISTER_MESSAGE)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Registration error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed",
        ) from exc


@router.post(
    Api.AUTH_RESEND_VERIFICATION,
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("5/hour")
async def resend_verification(
    request: Request,  # pylint: disable=unused-argument
    body: EmailRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Send a fresh verification link; the previous one stops working."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is not None and not bool(user.is_verified):
        verification_token = generate_verification_token()
        user.verification_token = token_fingerprint(verification_token)  # type: ignore[assignment]
        user.verification_expires_at = _verification_deadline()  # type: ignore[assignment]
        await db.commit()
        await asyncio.to_thread(
            send_verification_email,
            body.email,
            verification_token,
            name=str(user.name),
        )
        logger.info("Verification email re-sent (ID: %s)", user.id)
    return MessageResponse(message=RESEND_MESSAGE)


@router.post(
    Api.AUTH_FORGOT_PASSWORD,
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("5/hour")
async def forgot_password(
    request: Request,  # pylint: disable=unused-argument
    body: EmailRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Email a one-time reset link. Only the token's SHA-256 is stored."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is not None:
        reset_token = generate_verification_token()
        user.password_reset_token = token_fingerprint(reset_token)  # type: ignore[assignment]
        user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(  # type: ignore[assignment]
            minutes=get_settings().password_reset_token_expire_minutes
        )
        await db.commit()
        await asyncio.to_thread(
            send_password_reset_email, body.email, reset_token, name=str(user.name)
        )
        logger.info("Password reset email sent (ID: %s)", user.id)
    return MessageResponse(message=FORGOT_MESSAGE)


@router.post(
    Api.AUTH_LOGIN, response_model=TokenResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("20/hour")
async def login(
    request: Request,  # pylint: disable=unused-argument
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """Login user and return access and refresh tokens."""
    try:
        logger.info("Login attempt")

        # Find user by email
        stmt = select(User).where(User.email == credentials.email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        # An account created through Google or Apple has no password.
        hashed_password = (
            str(user.hashed_password) if user and user.hashed_password else _DUMMY_HASH
        )
        password_ok = verify_password(credentials.password, hashed_password)
        if user is None or not user.hashed_password or not password_ok:
            logger.warning("Login failed: invalid credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        # Only someone who knows the password learns the account is unverified,
        # so this does not help enumerate accounts; it lets the app offer to
        # resend the verification email.
        if not user.is_verified:  # type: ignore[attr-defined]
            logger.warning("Login refused: email not verified (ID: %s)", user.id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email not verified",
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
            name=str(user.name),
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


async def _issue_session(db: AsyncSession, user: User) -> TokenResponse:
    """Start a new refresh family for a user who has just proved who they are."""
    user_id = str(user.id)
    family_id = uuid4()
    refresh = create_refresh_token(data={"sub": user_id, "family": str(family_id)})
    payload = decode_token(refresh)
    if payload is None:
        raise RuntimeError("Newly issued refresh token could not be decoded")
    db.add(
        RefreshSession(
            token_hash=token_fingerprint(refresh),
            family_id=family_id,
            user_id=user.id,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    )
    await db.commit()
    return TokenResponse(
        access_token=create_access_token(data={"sub": user_id}),
        refresh_token=refresh,
        name=str(user.name),
        user_id=user_id,
    )


async def _google_claims(id_token: str) -> tuple[str, str | None, bool, str | None]:
    """Verify a Google ID token: (subject, email, email_verified, name)."""
    try:
        # google-auth is synchronous and fetches Google's keys over the network.
        claims = await asyncio.to_thread(verify_google_id_token, id_token)
    except GoogleOAuthConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not available.",
        ) from exc
    except ValueError as exc:
        logger.warning("Google token rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token"
        ) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Google token verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is temporarily unavailable",
        ) from exc
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token"
        )
    email = claims.get("email")
    name = claims.get("name")
    return (
        sub,
        email.strip().lower() if isinstance(email, str) else None,
        claims.get("email_verified") in (True, "true"),
        name.strip()[:100] if isinstance(name, str) and name.strip() else None,
    )


async def _social_sign_in(
    db: AsyncSession,
    *,
    provider: str,
    subject: str,
    email: str | None,
    email_verified: bool,
    name: str | None,
) -> TokenResponse:
    """Sign in, link or create the account for one verified provider identity.

    The identity is the provider's subject, never the email address: an
    address can be reassigned, a subject cannot.
    """
    column: Any = User.google_sub if provider == "google" else User.apple_sub
    user = (
        await db.execute(select(User).where(column == subject))
    ).scalar_one_or_none()

    if user is None:
        if not email or not email_verified:
            # Without a verified address there is nothing safe to attach to.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your account did not share a verified email address.",
            )
        user = (
            await db.execute(select(User).where(User.email == email).with_for_update())
        ).scalar_one_or_none()
        if user is None:
            user = User(
                name=name or email.split("@")[0][:100],
                email=email,
                hashed_password=None,
                is_verified=True,
            )
            db.add(user)
            logger.info("Account created through %s sign-in", provider)
        else:
            if not bool(user.is_verified):
                # Someone registered this address but never proved they own
                # it. The provider has now proved who does: the unproven
                # password must not keep working.
                user.hashed_password = None  # type: ignore[assignment]
                user.verification_token = None  # type: ignore[assignment]
                user.verification_expires_at = None  # type: ignore[assignment]
                user.is_verified = True  # type: ignore[assignment]
            logger.info("%s identity linked to an existing account", provider)
        setattr(user, f"{provider}_sub", subject)
        await db.flush()

    return await _issue_session(db, user)


@router.post(
    Api.AUTH_GOOGLE, response_model=TokenResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("20/hour")
async def google_login(
    request: Request,  # pylint: disable=unused-argument
    body: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Sign in with Google (the Android app). The token is verified here."""
    subject, email, email_verified, name = await _google_claims(body.id_token)
    return await _social_sign_in(
        db,
        provider="google",
        subject=subject,
        email=email,
        email_verified=email_verified,
        name=name,
    )


@router.post(
    Api.AUTH_APPLE, response_model=TokenResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("20/hour")
async def apple_login(
    request: Request,  # pylint: disable=unused-argument
    body: AppleLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Sign in with Apple (the iOS app). The token is verified here."""
    claims = await verify_apple_identity_token(body.identity_token)
    return await _social_sign_in(
        db,
        provider="apple",
        subject=claims.sub,
        email=claims.email,
        email_verified=claims.email_verified,
        name=body.name.strip() if body.name and body.name.strip() else None,
    )


def _page(
    template: str, status_code: int, *, form: bool = False, **context
) -> HTMLResponse:
    """Render a branded account page.

    The API's default CSP blocks all inline styles, so these pages get their
    own policy that only allows the nonce'd <style> block they ship with (and,
    for the reset form, posting back to this origin).
    """
    nonce = secrets.token_urlsafe(16)
    html = render_template(template, csp_nonce=nonce, **context)
    form_action = "'self'" if form else "'none'"
    return HTMLResponse(
        content=html,
        status_code=status_code,
        headers={
            "Content-Security-Policy": (
                f"default-src 'none'; style-src 'nonce-{nonce}'; "
                f"base-uri 'none'; form-action {form_action}; frame-ancestors 'none'"
            ),
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


def _verification_page(state: str, status_code: int) -> HTMLResponse:
    return _page("account_result.html", status_code, state=state)


@web_router.get(
    Web.VERIFY_EMAIL, response_class=HTMLResponse, status_code=status.HTTP_200_OK
)
async def verify_email(
    token: Optional[str] = None, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Verify user email with the token from the emailed link; renders an HTML page."""
    try:
        logger.info("Email verification attempt")

        if not token:
            logger.warning("Email verification failed: missing token")
            return _verification_page("invalid", status.HTTP_400_BAD_REQUEST)

        stmt = select(User).where(User.verification_token == token_fingerprint(token))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning("Email verification failed: invalid token")
            return _verification_page("invalid", status.HTTP_400_BAD_REQUEST)

        if bool(user.is_verified):
            logger.info("Email already verified (ID: %s)", user.id)
            return _verification_page("already_verified", status.HTTP_200_OK)

        expires_at = user.verification_expires_at
        # Links issued before expiry existed carry no deadline; honour them.
        if expires_at is not None and _aware(expires_at) < datetime.now(timezone.utc):
            logger.warning("Email verification failed: expired link (ID: %s)", user.id)
            return _verification_page("expired", status.HTTP_400_BAD_REQUEST)

        # Update user as verified
        user.is_verified = True  # type: ignore[assignment]
        user.verification_token = None  # type: ignore[assignment]
        user.verification_expires_at = None  # type: ignore[assignment]
        user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        await db.commit()

        logger.info("Email verified successfully (ID: %s)", user.id)

        return _verification_page("verified", status.HTTP_200_OK)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Email verification error: %s", exc, exc_info=True)
        return _verification_page("error", status.HTTP_500_INTERNAL_SERVER_ERROR)


async def _user_for_reset_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    result = await db.execute(
        select(User)
        .where(User.password_reset_token == token_fingerprint(token))
        .with_for_update()
    )
    user = result.scalar_one_or_none()
    if user is None or user.password_reset_expires_at is None:
        return None
    if _aware(user.password_reset_expires_at) < datetime.now(timezone.utc):
        return None
    return user


def _reset_form(token: str, status_code: int, error: str = "") -> HTMLResponse:
    return _page(
        "reset_password.html",
        status_code,
        form=True,
        token=token,
        error=error,
        form_action=Web.RESET_PASSWORD_FORM,
        min_length=PASSWORD_MIN_LENGTH,
    )


@web_router.get(Web.RESET_PASSWORD_PAGE, response_class=HTMLResponse)
@limiter.limit("30/hour")
async def reset_password_page(
    request: Request,  # pylint: disable=unused-argument
    token: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """The page the emailed reset link opens."""
    if await _user_for_reset_token(token, db) is None or token is None:
        return _verification_page("reset_invalid", status.HTTP_400_BAD_REQUEST)
    return _reset_form(token, status.HTTP_200_OK)


@web_router.post(Web.RESET_PASSWORD_FORM, response_class=HTMLResponse)
@limiter.limit("10/hour")
async def reset_password_submit(
    request: Request,  # pylint: disable=unused-argument
    token: str = Form(..., max_length=200),
    password: str = Form(..., max_length=200),
    confirm_password: str = Form(..., max_length=200),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Set the new password, then sign the account out everywhere."""
    user = await _user_for_reset_token(token, db)
    if user is None:
        return _verification_page("reset_invalid", status.HTTP_400_BAD_REQUEST)
    if password != confirm_password:
        return _reset_form(
            token, status.HTTP_400_BAD_REQUEST, "The two passwords do not match."
        )
    if len(password) < PASSWORD_MIN_LENGTH:
        return _reset_form(
            token,
            status.HTTP_400_BAD_REQUEST,
            f"Use at least {PASSWORD_MIN_LENGTH} characters.",
        )
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return _reset_form(
            token, status.HTTP_400_BAD_REQUEST, "That password is too long."
        )

    now = datetime.now(timezone.utc)
    user.hashed_password = get_password_hash(password)  # type: ignore[assignment]
    user.password_reset_token = None  # type: ignore[assignment]
    user.password_reset_expires_at = None  # type: ignore[assignment]
    user.password_changed_at = now  # type: ignore[assignment]
    user.updated_at = now  # type: ignore[assignment]
    # Opening the emailed link proves control of the inbox, which is all that
    # verification ever established.
    user.is_verified = True  # type: ignore[assignment]
    user.verification_token = None  # type: ignore[assignment]
    user.verification_expires_at = None  # type: ignore[assignment]
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()
    logger.info("Password reset completed (ID: %s)", user.id)
    return _verification_page("reset_done", status.HTTP_200_OK)


@router.post(
    Api.AUTH_REFRESH, response_model=TokenResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("120/hour")
async def refresh_token(
    request: Request,  # pylint: disable=unused-argument
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Refresh access token using refresh token. Requires valid refresh token."""
    try:
        token = body.refresh_token
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


@router.post(
    Api.AUTH_LOGOUT, response_model=MessageResponse, status_code=status.HTTP_200_OK
)
async def logout(
    body: RefreshTokenRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Logout user by blacklisting access and refresh tokens."""
    try:
        logger.info("Logout attempt (ID: %s)", current_user.id)

        access_token = credentials.credentials
        user_refresh_token = body.refresh_token

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


async def _owns_account(user: User, proof: DeleteAccountRequest) -> bool:
    """Fresh proof for a destructive action: the password, or a new token for
    the Google or Apple identity already linked to this account."""
    if proof.password is not None:
        return bool(user.hashed_password) and verify_password(
            proof.password, str(user.hashed_password)
        )
    if proof.google_id_token is not None and user.google_sub:
        subject, *_ = await _google_claims(proof.google_id_token)
        return subject == user.google_sub
    if proof.apple_identity_token is not None and user.apple_sub:
        claims = await verify_apple_identity_token(proof.apple_identity_token)
        return claims.sub == user.apple_sub
    return False


@router.delete(
    Api.AUTH_ACCOUNT, response_model=MessageResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("10/hour")
async def delete_account(
    request: Request,  # pylint: disable=unused-argument
    body: DeleteAccountRequest,
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
    if not await _owns_account(locked_user, body):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    try:
        await delete_user_pdf_data(locked_user.id, db)
        # Redis client calls block; keep them off the event loop.
        await asyncio.to_thread(
            mark_account_deleted_and_purge_jobs, str(locked_user.id)
        )
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
