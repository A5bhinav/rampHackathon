"""Svix signatures, the scheme Resend signs its webhooks with (spec 0085).

Signed content is `<svix-id>.<svix-timestamp>.<raw body>`; the key is the base64 part of the
`whsec_...` secret; the signature is base64(HMAC-SHA256). `svix-signature` holds space-separated
`v1,<signature>` items (several during a key rotation). Any one matching item is enough.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping

MIN_KEY_BYTES = 16


def decode_secret(secret: str) -> bytes:
    """The HMAC key inside a `whsec_...` secret. Raises ValueError for anything that is not base64."""
    try:
        return base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("not a base64 signing secret") from exc


def verify(secret: str, headers: Mapping[str, str], raw: bytes, now: int, tolerance: int = 300) -> bool:
    """True only for a fresh, correctly signed delivery. Every malformed input is a refusal."""
    lowered = {k.lower(): v for k, v in headers.items()}
    msg_id, stamp, given = lowered.get("svix-id"), lowered.get("svix-timestamp"), lowered.get("svix-signature")
    if not msg_id or not stamp or not given:
        return False
    try:
        ts = int(stamp)
        key = decode_secret(secret)
    except ValueError:
        return False
    if not key:  # an empty key would let anyone forge a delivery
        return False
    if abs(now - ts) > tolerance:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + raw, hashlib.sha256).digest())
    ok = False
    for item in given.split():
        version, _, sig = item.partition(",")
        if version == "v1" and hmac.compare_digest(expected, sig.encode("utf-8", errors="replace")):
            ok = True  # no early exit: keep the time independent of which item matched
    return ok
