"""dialback-ledger. See the README in this folder.

The core reaches this package by name (apps/core/src/dialback_core/ports.py):
`select_number_stub` under DIALBACK_RESOLVER=stub, `select_number` under `ledger` (not here
until ledger-select-v1 lands, so that mode fails closed), and `change_context`.
"""
from .select import ChangeRequest, DocumentRef, Settings, Sighting, select
from .stub import change_context, select_number_stub

__all__ = ["ChangeRequest", "DocumentRef", "Settings", "Sighting", "change_context", "select", "select_number_stub"]
