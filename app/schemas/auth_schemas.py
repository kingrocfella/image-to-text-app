"""Authentication schemas."""

from pydantic import BaseModel, EmailStr, Field, field_validator


def _validate_bcrypt_password(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return value


class UserRegister(BaseModel):
    """User registration schema."""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)

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


class DeleteAccountRequest(BaseModel):
    """Require fresh knowledge of the password for destructive account removal."""

    password: str = Field(..., min_length=1, max_length=72)

    _password_bytes = field_validator("password")(_validate_bcrypt_password)


class MessageResponse(BaseModel):
    """Message response schema."""

    message: str
