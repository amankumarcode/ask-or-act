"""Northwind traps. Importing a module registers its trap as "northwind/<id>".

The order they are applied in comes from datasets/northwind/dataset.yaml, not from here.
"""

from askoract.datasets.northwind.traps import (  # noqa: F401
    active_customer,
    amount_in_cents,
    cancelled_status,
    duplicate_join,
    legacy_customers,
)
