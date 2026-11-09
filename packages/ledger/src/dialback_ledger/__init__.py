"""dialback-ledger. See the README in this folder.

The core reaches this package by name (apps/core/src/dialback_core/ports.py):
`select_number_stub` under DIALBACK_RESOLVER=stub, `select_number` under `ledger` (spec 0024),
and `change_context`. Both database calls refuse rather than raise (R7).
"""
from .select import ChangeRequest, DocumentRef, Settings, Sighting, select
from .store import change_context, select_number
from .stub import select_number_stub

__all__ = ["ChangeRequest", "DocumentRef", "Settings", "Sighting", "change_context", "select", "select_number",
           "select_number_stub"]
