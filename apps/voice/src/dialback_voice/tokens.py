"""The one-time call token (design, "How a call runs", step 2). Minted before dialing and bound to
the case, the attempt and the dialed number, then passed to Twilio as a <Parameter> and checked
when ConversationRelay connects, so a replayed or cross-wired connection is refused. It is a
keyed HMAC over the three facts: stateless, and it reveals none of them. On the text line there is
no Twilio, so the token is minted and verified the same way but never leaves the process."""
from __future__ import annotations

import hashlib
import hmac
from uuid import UUID


def _message(case_id: UUID, attempt: int, number_e164: str) -> bytes:
    return f"{case_id}:{attempt}:{number_e164}".encode()


def mint(key: bytes, *, case_id: UUID, attempt: int, number_e164: str) -> str:
    """A hex token bound to exactly this case, attempt and number."""
    return hmac.new(key, _message(case_id, attempt, number_e164), hashlib.sha256).hexdigest()


def verify(key: bytes, token: str, *, case_id: UUID, attempt: int, number_e164: str) -> bool:
    """True only if `token` was minted for this same call. Constant-time, so a wrong token leaks
    nothing by timing."""
    expected = mint(key, case_id=case_id, attempt=attempt, number_e164=number_e164)
    return hmac.compare_digest(token, expected)
