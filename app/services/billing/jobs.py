"""Background loop that keeps Android subscriptions current (docs/billing.md).

Google pushes nothing to us (Apple does, via the webhook), so the API process
re-reads Play subscriptions near expiry every hour and sweeps refunds every
six hours. Runs only with BILLING_PROVIDER=store.
"""

import asyncio

from app.database.postgres import AsyncSessionLocal
from app.services.billing.service import (
    billing_deps,
    refresh_google_subscriptions,
    sweep_google_voided,
)
from app.utils.logger import logger

REFRESH_INTERVAL_S = 3600
VOIDED_EVERY_N_REFRESHES = 6


async def google_billing_loop() -> None:
    google = billing_deps().google
    if google is None:
        return
    tick = 0
    while True:
        try:
            async with AsyncSessionLocal() as session:
                checked, active = await refresh_google_subscriptions(session, google)
                if checked:
                    logger.info(
                        "Play subscriptions refreshed checked=%d active=%d",
                        checked,
                        active,
                    )
                if tick % VOIDED_EVERY_N_REFRESHES == 0:
                    seen, ended = await sweep_google_voided(session, google)
                    logger.info("Play refunds swept seen=%d ended=%d", seen, ended)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Play billing refresh failed", exc_info=True)
        tick += 1
        await asyncio.sleep(REFRESH_INTERVAL_S)
