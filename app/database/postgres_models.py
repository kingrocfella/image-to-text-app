"""Database models."""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database.postgres import Base  # noqa: F401


class User(Base):
    """User model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    # Null for an account that has only ever signed in with Google or Apple.
    hashed_password = Column(String(255), nullable=True)
    # Provider subject IDs. Identities are keyed by these, never by email.
    google_sub = Column(String(255), unique=True, nullable=True, index=True)
    apple_sub = Column(String(255), unique=True, nullable=True, index=True)
    # ScanGenAI Pro granted without a purchase (docs/billing.md).
    pro_until = Column(DateTime(timezone=True), nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_token = Column(String(255), nullable=True, index=True)
    verification_expires_at = Column(DateTime(timezone=True), nullable=True)
    # SHA-256 of a one-time password-reset token; never the token itself.
    password_reset_token = Column(String(64), nullable=True, index=True)
    password_reset_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Access tokens issued before this moment are rejected (password reset).
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class TokenBlacklist(Base):
    """Token blacklist model."""

    __tablename__ = "token_blacklist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    token = Column(Text, nullable=False, unique=True, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class RefreshSession(Base):
    """Server-side state for one-time refresh-token rotation."""

    __tablename__ = "refresh_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    family_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by_hash = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class PDFRequest(Base):
    """PDF request model for storing request_id to collection_name mapping."""

    __tablename__ = "pdf_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    request_id = Column(String(36), unique=True, nullable=False, index=True)
    collection_name = Column(String(255), nullable=False)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=True)
    # The embedding model that built the collection; null means the legacy one.
    embedding_model = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class UsageCounter(Base):
    """How many of one kind of job a user has started in one calendar month."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("user_id", "period", "kind", name="uq_usage_counters_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # UTC calendar month, "YYYY-MM".
    period = Column(String(7), nullable=False)
    kind = Column(String(32), nullable=False)
    count = Column(Integer, nullable=False, default=0)


class JobRun(Base):
    """Durable record of one background job, for the operations monitor.

    Redis holds the job itself and its result; both expire. This row is what
    lets the monitor and the admin console see failures and stuck work. It
    carries no user content and is deleted with the account.
    """

    __tablename__ = "job_runs"

    message_id = Column(String(64), primary_key=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_type = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="queued", index=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
        index=True,
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
