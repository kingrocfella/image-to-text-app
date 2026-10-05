"""SECRET_KEY rotation keeps existing sessions valid (docs/operations.md)."""

from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings, override_settings
from app.utils import create_access_token, decode_token

OLD_KEY = "old-key-" + "o" * 40
NEW_KEY = "new-key-" + "n" * 40


def _token_signed_with(key: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "user",
            "type": "access",
            "jti": "id",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        key,
        algorithm="HS256",
    )


def test_a_token_signed_with_the_previous_key_still_verifies():
    token = _token_signed_with(OLD_KEY)

    with override_settings(secret_key=NEW_KEY, secret_key_previous=OLD_KEY):
        assert decode_token(token)["sub"] == "user"
    # Once the previous key is dropped (the next rotation), it stops verifying.
    with override_settings(secret_key=NEW_KEY, secret_key_previous=""):
        assert decode_token(token) is None


def test_new_tokens_are_signed_with_the_current_key_only():
    with override_settings(secret_key=NEW_KEY, secret_key_previous=OLD_KEY):
        token = create_access_token({"sub": "user"})

    settings = get_settings()
    decoded = jwt.decode(
        token,
        NEW_KEY,
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    assert decoded["sub"] == "user"


def test_a_token_signed_with_an_unknown_key_is_rejected():
    token = _token_signed_with("unknown-" + "u" * 40)
    with override_settings(secret_key=NEW_KEY, secret_key_previous=OLD_KEY):
        assert decode_token(token) is None


def test_an_expired_token_is_rejected_whichever_key_signed_it():
    settings = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    token = jwt.encode(
        {
            "sub": "user",
            "type": "access",
            "jti": "id",
            "iat": past,
            "exp": past + timedelta(minutes=5),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        OLD_KEY,
        algorithm="HS256",
    )
    with override_settings(secret_key=NEW_KEY, secret_key_previous=OLD_KEY):
        assert decode_token(token) is None
