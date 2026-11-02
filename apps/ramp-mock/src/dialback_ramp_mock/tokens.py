"""Per-dataset bearer tokens (spec 0002, D1).

A token is `mock.<dataset_id>.<hex HMAC-SHA256(secret, dataset_id)>`. Whoever holds the
shared secret (the core, the lab) can mint a token for a dataset; the mock checks it and
serves only that dataset's business, so parallel scenarios can never read each other's
vendors. Nothing is stored. The core mints the same format in its Ramp adapter.
"""
from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

PREFIX = "mock"


def mint(secret: bytes, dataset_id: UUID) -> str:
    sig = hmac.new(secret, str(dataset_id).encode(), hashlib.sha256).hexdigest()
    return f"{PREFIX}.{dataset_id}.{sig}"


def verify(secret: bytes, token: str) -> UUID | None:
    """Return the dataset id a valid token names, or None."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    try:
        dataset_id = UUID(parts[1])
    except ValueError:
        return None
    expected = hmac.new(secret, str(dataset_id).encode(), hashlib.sha256).hexdigest()
    return dataset_id if hmac.compare_digest(expected, parts[2]) else None
