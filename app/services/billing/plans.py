"""ScanGenAI Pro: the products the app sells (docs/billing.md).

The product IDs must match App Store Connect, the Play Console, and
``image-text-react/src/constants`` exactly; tests/test_billing_contract.py
fails if the app and the server disagree. They carry a ``scangenai_`` prefix
because App Store product IDs are unique across the whole developer team.
"""

from typing import Final

PRO_MONTHLY_PRODUCT_ID: Final = "scangenai_pro_monthly"
PRO_YEARLY_PRODUCT_ID: Final = "scangenai_pro_yearly"
PRO_PRODUCT_IDS: Final = frozenset({PRO_MONTHLY_PRODUCT_ID, PRO_YEARLY_PRODUCT_ID})
