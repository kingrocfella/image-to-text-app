"""Mobile diagnostic log batch schemas.

The payload is untrusted: every field is bounded here, and the service
re-sanitises the content before it reaches a log sink.
"""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ClientLogLevel = Literal["debug", "info", "warn", "error"]

MAX_CLIENT_LOG_RECORDS = 50
MAX_CLIENT_LOG_CONTEXT_BYTES = 4096
_NAME_PATTERN = r"^[A-Za-z0-9._-]{1,100}$"


class ClientLogRecord(BaseModel):
    """One structured event emitted by ``createMobileLogger``."""

    timestamp: datetime
    level: ClientLogLevel
    scope: str = Field(..., pattern=_NAME_PATTERN)
    event: str = Field(..., pattern=_NAME_PATTERN)
    message: str = Field(..., min_length=1, max_length=1000)
    runtime_id: str = Field(..., pattern=_NAME_PATTERN)
    context: dict[str, Any] | None = None

    @field_validator("context")
    @classmethod
    def _bounded_context(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value)) > MAX_CLIENT_LOG_CONTEXT_BYTES:
            raise ValueError("context is too large")
        return value


class ClientLogBatch(BaseModel):
    """A batch uploaded by the mobile log shipper."""

    records: list[ClientLogRecord] = Field(
        ..., min_length=1, max_length=MAX_CLIENT_LOG_RECORDS
    )


class ClientLogAccepted(BaseModel):
    accepted: int
