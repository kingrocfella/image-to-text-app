"""Mobile diagnostic log ingestion (AGENTS.md §4)."""

from fastapi import APIRouter, Depends, Request, status

from app.database import User
from app.dependencies import get_current_active_user
from app.paths import Api
from app.schemas.client_log_schemas import ClientLogAccepted, ClientLogBatch
from app.services.client_logs import ingest_client_logs
from app.utils.rate_limit import limiter

router = APIRouter(tags=["diagnostics"])


@router.post(
    Api.CLIENT_LOGS,
    response_model=ClientLogAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("30/minute")
async def upload_client_logs(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    batch: ClientLogBatch,
    current_user: User = Depends(get_current_active_user),
) -> ClientLogAccepted:
    """Accept a batch of already-redacted mobile log records."""
    return ClientLogAccepted(
        accepted=ingest_client_logs(batch.records, current_user.id)
    )
