"""Call answers on arrival (spec 0002, D7).

The voice service sends the spoken last-four digits in memory only. The core compares them
with the case's accounts, then replaces them with a keyed hash before anything is stored or
appended to the evidence chain. Neither the digits nor the request body are ever logged.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any
from uuid import UUID

NO_DIGITS = ("dont_know", "refused")


def spoken_last_four(transit: dict[str, Any] | None) -> list[str]:
    if not transit:
        return []
    return [a["value"] for a in transit.get("answers", []) if a["slot"] == "account_last4" and isinstance(a["value"], str)]


def digits_hmac(key: bytes, call_id: UUID, digits: str) -> str:
    """HMAC-SHA256 with the server key, salted with the call id so equal digits on two calls
    don't produce equal hashes."""
    return hmac.new(key, f"{call_id}:{digits}".encode(), hashlib.sha256).hexdigest()


def protect(transit: dict[str, Any], key: bytes, key_version: int) -> dict[str, Any]:
    """The stored call-answers shape: identical, except account_last4 digits become a keyed hash."""
    call_id = UUID(str(transit["call_id"]))
    stored = []
    for a in transit.get("answers", []):
        a = dict(a)
        if a["slot"] == "account_last4" and isinstance(a["value"], str) and a["value"] not in NO_DIGITS:
            a["value"] = {"digits_hmac": digits_hmac(key, call_id, a["value"]), "digit_count": len(a["value"]),
                          "key_version": key_version}
        stored.append(a)
    return {"schema_version": transit["schema_version"], "call_id": str(call_id), "answers": stored}
