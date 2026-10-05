"""The app and the server must sell the same products (docs/billing.md)."""

from pathlib import Path

import pytest

from app.services.billing.plans import PRO_PRODUCT_IDS

MOBILE_CONSTANTS = (
    Path(__file__).resolve().parent.parent.parent
    / "image-text-react"
    / "src"
    / "constants"
    / "index.ts"
)


def test_the_app_offers_exactly_the_products_the_server_accepts():
    if not MOBILE_CONSTANTS.exists():
        pytest.skip("the mobile repository is not checked out beside this one")
    source = MOBILE_CONSTANTS.read_text()
    for product_id in sorted(PRO_PRODUCT_IDS):
        assert f'"{product_id}"' in source
