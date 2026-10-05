"""Authentication utilities."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import bcrypt
import jwt

from app.config import get_settings

_settings = get_settings()

# JWT settings
SECRET_KEY = _settings.secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = _settings.access_token_expire_hours
REFRESH_TOKEN_EXPIRE_DAYS = _settings.refresh_token_expire_days
JWT_ISSUER = _settings.jwt_issuer
JWT_AUDIENCE = _settings.jwt_audience


def _verification_keys() -> list[str]:
    """Keys a token may be signed with: the current one, then the previous.

    `make rotate-secrets` moves the old SECRET_KEY to SECRET_KEY_PREVIOUS, which
    is accepted for verification only, so a rotation signs nobody out. New
    tokens are always signed with SECRET_KEY.
    """
    settings = get_settings()
    keys = [settings.secret_key]
    if settings.secret_key_previous:
        keys.append(settings.secret_key_previous)
    return keys


def _password_bytes(password: str) -> bytes:
    """Encode a password without silently collapsing distinct bcrypt inputs."""
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return password_bytes


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    try:
        password_bytes = _password_bytes(plain_password)
    except ValueError:
        return False
    # passlib format starts with $2b$, handle both passlib and raw bcrypt formats
    if hashed_password.startswith("$2"):
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    return False


def get_password_hash(password: str) -> str:
    """Hash a password.

    Passwords longer than bcrypt's 72-byte input limit are rejected.
    """
    password_bytes = _password_bytes(password)
    # Generate salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    issued_at = datetime.now(timezone.utc)
    to_encode.update(
        {
            "aud": JWT_AUDIENCE,
            "exp": expire,
            "iat": issued_at,
            "iss": JWT_ISSUER,
            "jti": str(uuid4()),
            "type": "access",
        }
    )
    encoded_jwt = jwt.encode(to_encode, get_settings().secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    issued_at = datetime.now(timezone.utc)
    to_encode.update(
        {
            "aud": JWT_AUDIENCE,
            "exp": expire,
            "iat": issued_at,
            "iss": JWT_ISSUER,
            "jti": str(uuid4()),
            "type": "refresh",
        }
    )
    encoded_jwt = jwt.encode(to_encode, get_settings().secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token against the current or previous key."""
    for key in _verification_keys():
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=JWT_AUDIENCE,
                issuer=JWT_ISSUER,
                options={"require": ["aud", "exp", "iat", "iss", "jti", "sub", "type"]},
            )
        except jwt.InvalidSignatureError:
            continue
        except jwt.InvalidTokenError:
            return None
    return None


def generate_verification_token() -> str:
    """Generate a random verification token."""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """Return a non-reversible token identifier safe to persist."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
