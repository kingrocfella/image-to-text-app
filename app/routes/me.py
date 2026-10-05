"""The signed-in account: plan, offered models and this month's usage."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import User, get_db
from app.dependencies import get_current_active_user
from app.paths import Api
from app.schemas import MeResponse
from app.services import quota
from app.services.billing.entitlement import Entitlement, get_entitlement
from app.services.billing.service import billing_deps
from app.utils.constants import available_models, pro_only_models

router = APIRouter(tags=["account"])


def login_methods(user: User) -> list[str]:
    methods = []
    if user.hashed_password:
        methods.append("password")
    if user.google_sub:
        methods.append("google")
    if user.apple_sub:
        methods.append("apple")
    return methods


async def build_me(
    db: AsyncSession, user: User, entitlement: Entitlement | None = None
) -> MeResponse:
    settings = get_settings()
    user_id = cast(UUID, user.id)
    entitlement = entitlement or await get_entitlement(db, user_id)
    return MeResponse(
        user_id=str(user.id),
        name=str(user.name),
        email=str(user.email),
        login_methods=login_methods(user),
        pro=entitlement.pro,
        pro_source=entitlement.source,
        pro_product_id=entitlement.product_id,
        pro_expires_at=entitlement.expires_at,
        purchases_available=billing_deps().store is not None,
        models=available_models(settings, entitlement.pro),
        pro_models=pro_only_models(settings, entitlement.pro),
        usage=await quota.usage(db, user_id, entitlement.pro),  # type: ignore[arg-type]
        pro_limits=quota.quota_limits(settings, pro=True),
    )


@router.get(Api.ME, response_model=MeResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """The server decides the plan, the models and what is left; the app shows it."""
    return await build_me(db, current_user)
