"""Authentication schemas."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72


def _validate_bcrypt_password(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return value


class UserRegister(BaseModel):
    """User registration schema."""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=72)

    _password_bytes = field_validator("password")(_validate_bcrypt_password)


class UserLogin(BaseModel):
    """User login schema."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)

    _password_bytes = field_validator("password")(_validate_bcrypt_password)


class TokenResponse(BaseModel):
    """Token response schema."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    name: str
    user_id: str


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema."""

    refresh_token: str


class GoogleLoginRequest(BaseModel):
    """A Google ID token from the Android app."""

    id_token: str = Field(..., min_length=1, max_length=8192)


class AppleLoginRequest(BaseModel):
    """A Sign in with Apple identity token from the iOS app.

    Apple gives the person's name to the app once, on first authorisation,
    and never puts it in the token, so the app passes it along.
    """

    identity_token: str = Field(..., min_length=1, max_length=8192)
    name: str | None = Field(None, max_length=100)


class DeleteAccountRequest(BaseModel):
    """Fresh proof that the person deleting the account owns it.

    Exactly one: the current password, or a new Google / Apple token for the
    identity linked to the account. A bearer token alone is never enough.
    """

    password: str | None = Field(None, min_length=1, max_length=72)
    google_id_token: str | None = Field(None, min_length=1, max_length=8192)
    apple_identity_token: str | None = Field(None, min_length=1, max_length=8192)

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, value: str | None) -> str | None:
        return None if value is None else _validate_bcrypt_password(value)


class EmailRequest(BaseModel):
    """An email address, for resend-verification and forgot-password."""

    email: EmailStr


class QuotaUsage(BaseModel):
    used: int
    limit: int


class MeResponse(BaseModel):
    """What the app needs to render the account screen and the model picker.

    The server decides every field: the plan, which models this account may
    use, which ones need Pro, and how much of each allowance is left.
    """

    user_id: str
    name: str
    email: str
    # How this account can prove who it is: "password", "google", "apple".
    login_methods: list[str]
    pro: bool
    pro_source: str | None
    pro_product_id: str | None
    pro_expires_at: datetime | None
    # False while BILLING_PROVIDER=off: the paywall says plans are coming.
    purchases_available: bool
    models: list[str]
    # Configured models this account cannot use until it is on Pro.
    pro_models: list[str]
    usage: dict[str, QuotaUsage]
    # What Pro would give, for the paywall: {kind: limit}.
    pro_limits: dict[str, int]


class MessageResponse(BaseModel):
    """Message response schema."""

    message: str
