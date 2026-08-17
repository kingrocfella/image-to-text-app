"""Authentication utilities."""

import hmac
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import bcrypt
import jwt
from dotenv import load_dotenv

load_dotenv()

# JWT settings
_INSECURE_SECRET_VALUES = {
    "change-me",
    "changeme",
    "secret-key",
    "your-openai-pass",
    "your-secret-key",
}


def _required_secret(name: str, minimum_length: int) -> str:
    """Load a required secret and reject missing or placeholder values."""
    value = os.getenv(name, "").strip()
    normalized = value.lower()
    if (
        len(value) < minimum_length
        or normalized in _INSECURE_SECRET_VALUES
        or normalized.startswith(("change-me", "your-"))
    ):
        raise RuntimeError(
            f"{name} must be configured with at least {minimum_length} non-placeholder characters"
        )
    return value


SECRET_KEY = _required_secret("SECRET_KEY", 32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "1"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "1"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "scangenai-api").strip()
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "scangenai-client").strip()

if not JWT_ISSUER or not JWT_AUDIENCE:
    raise RuntimeError("JWT_ISSUER and JWT_AUDIENCE must be configured")

if os.getenv("ENVIRONMENT", "development").strip().lower() in {"prod", "production"}:
    _required_secret("OPENAI_PASS", 16)


def verify_openai_password(provided_password: str | None) -> bool:
    """Validate the API-side OpenAI access password without leaking it to jobs."""
    if provided_password is None:
        return False
    try:
        configured_password = _required_secret("OPENAI_PASS", 16)
    except RuntimeError:
        return False
    return hmac.compare_digest(provided_password, configured_password)


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
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
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
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token."""
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["aud", "exp", "iat", "iss", "jti", "sub", "type"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def generate_verification_token() -> str:
    """Generate a random verification token."""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """Return a non-reversible token identifier safe to persist."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
