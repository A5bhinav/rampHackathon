"""The Nov 1 stand-ins the core calls under DIALBACK_RESOLVER=stub (spec 0018).

`select_number_stub` returns the contract's `selection.picked.json` example (copied beside this
file so the package works without the contracts folder; a test checks the copy hasn't drifted)
with the case's own ids and cutoff. It is a labeled stub: `rule_version` says `ledger-stub-v0`,
and its contact point exists in no table.

The real `change_context` and `select_number` are in store.py (spec 0024).
"""
from __future__ import annotations

import datetime as dt
import json
from importlib.resources import files
from typing import Any
from uuid import UUID, uuid5

STUB_RULE_VERSION = "ledger-stub-v0"
STUB_NAMESPACE = UUID("9a3c5e7f-1b2d-4f6a-8c0e-2d4f6a8c0e1b")
_EXAMPLE = files(__package__).joinpath("stub_selection.json").read_text(encoding="utf-8")


def _utc(ts: dt.datetime) -> str:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("cutoff must carry a time zone; a naive time is never guessed")
    return ts.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def select_number_stub(*, case_id: UUID, dataset_id: UUID, vendor_id: UUID, cutoff: dt.datetime) -> dict[str, Any]:
    selection = json.loads(_EXAMPLE)
    at = _utc(cutoff)
    selection.update(case_id=str(case_id), dataset_id=str(dataset_id), vendor_id=str(vendor_id),
                     requested_cutoff=at, cutoff=at, rule_version=STUB_RULE_VERSION,
                     # one selection id per case, so a stored stub selection never collides with another case's
                     number_selection_id=str(uuid5(STUB_NAMESPACE, f"{case_id}:{STUB_RULE_VERSION}")))
    return selection

