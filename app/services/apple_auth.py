"""Verify Sign in with Apple identity tokens."""

import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, status

from app.config import get_settings
from app.utils.logger import logger

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_JWKS_CACHE_SECONDS = 3600
DEFAULT_APPLE_AUDIENCE = "com.leonfrontier.scangenai"

_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires_at = 0.0


@dataclass(frozen=True)
class AppleClaims:
    """Normalized Apple identity claims used by the auth route."""

    sub: str
    email: str | None
    email_verified: bool
    is_private_relay: bool


def _audience() -> str:
    return get_settings().ios_bundle_id or DEFAULT_APPLE_AUDIENCE


async def _fetch_jwks(*, force: bool = False) -> dict[str, Any]:
    """Fetch and cache Apple's public signing keys."""
    global _jwks_cache, _jwks_cache_expires_at

    now = time.monotonic()
    if not force and _jwks_cache and now < _jwks_cache_expires_at:
        return _jwks_cache

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(APPLE_JWKS_URL)
        res.raise_for_status()
        data = res.json()

    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise ValueError("Apple JWKS response is malformed")

    _jwks_cache = data
    _jwks_cache_expires_at = now + APPLE_JWKS_CACHE_SECONDS
    return data


def _key_for_kid(jwks: dict[str, Any], kid: str) -> Any:
    for jwk in jwks.get("keys", []):
        if isinstance(jwk, dict) and jwk.get("kid") == kid:
            return jwt.PyJWK.from_dict(jwk).key
    return None


def _bool_claim(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.lower() == "true"
    return False


async def _signing_key(kid: str) -> Any:
    """Apple's key for this token, refetching once in case the keys rotated."""
    key = _key_for_kid(await _fetch_jwks(), kid)
    if key is None:
        key = _key_for_kid(await _fetch_jwks(force=True), kid)
    if key is None:
        logger.warning("Apple token rejected: unknown key id")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Apple token",
        )
    return key


async def verify_apple_identity_token(identity_token: str) -> AppleClaims:
    """Cryptographically verify an Apple identity token and return claims."""
    try:
        header = jwt.get_unverified_header(identity_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Apple token",
        ) from exc

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Apple token",
        )

    try:
        key = await _signing_key(kid)

        claims = jwt.decode(
            identity_token,
            key=key,
            # Apple signs identity tokens with RS256.  ES256 is only used for
            # the server-to-server notification key, which is a different flow.
            algorithms=["RS256"],
            audience=_audience(),
            issuer=APPLE_ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Apple JWKS fetch/parse failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Apple sign-in is temporarily unavailable",
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning("Apple token rejected: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Apple token",
        ) from exc

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apple token missing subject",
        )

    raw_email = claims.get("email")
    email = raw_email.strip().lower() if isinstance(raw_email, str) else None
    return AppleClaims(
        sub=sub.strip(),
        email=email,
        email_verified=_bool_claim(claims.get("email_verified")),
        is_private_relay=bool(email and email.endswith("@privaterelay.appleid.com")),
    )
