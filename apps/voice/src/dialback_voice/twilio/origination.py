"""Starting a Twilio ConversationRelay call, and trusting Twilio's webhooks.

`build_connect_twiml` writes the TwiML the voice service returns when Twilio fetches a call's
instructions: `<Connect><ConversationRelay>` with the fixed disclosure as the welcomeGreeting (so it
plays first, R8) and the one-time call_token as a `<Parameter>`. The case id and the dialed number's
lineage are never written into it. `valid_twilio_signature` checks the X-Twilio-Signature that
Twilio puts on its webhook requests, so a forged POST moves nothing (R9). `status_result` turns a
call that never reached a live conversation into the result the core is told.

The REST call that actually dials (passing this TwiML's URL and the status-callback URL) is the
live piece, done by the ops-provisioned Twilio client; it is not in this module because it cannot
run or be tested without a live account (spec 0013, Q47/Q48)."""
from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from xml.sax.saxutils import quoteattr


def build_connect_twiml(*, ws_url: str, call_token: str, disclosure: str) -> str:
    """The TwiML that connects the call to our ConversationRelay socket. `quoteattr` escapes every
    value, so the disclosure's punctuation cannot break out of its attribute."""
    # interruptible="false" keeps the disclosure from being cut off (R8). Whether the greeting alone
    # can be non-interruptible while the rest of the call is interruptible is Q45; until that is
    # confirmed the whole call is non-interruptible, which is the safe side.
    attrs = (f"url={quoteattr(ws_url)} welcomeGreeting={quoteattr(disclosure)} "
             f'dtmfDetection="true" interruptible="false"')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f"<ConversationRelay {attrs}>"
            f"<Parameter name=\"call_token\" value={quoteattr(call_token)}/>"
            "</ConversationRelay>"
            "</Connect></Response>")


def valid_twilio_signature(auth_token: bytes, signature: str, url: str, params: Mapping[str, str]) -> bool:
    """True if `signature` is Twilio's X-Twilio-Signature for this request: HMAC-SHA1, keyed by the
    account's auth token, over the full URL followed by each POST field as key+value in key order,
    base64-encoded (Twilio's published scheme). Constant-time, so a wrong signature leaks nothing."""
    data = url + "".join(key + params[key] for key in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token, data.encode("utf-8"), hashlib.sha1).digest()).decode("ascii")
    return hmac.compare_digest(expected, signature or "")


def status_result(call_status: str, answered_by: str | None) -> tuple[str, str | None] | None:
    """Map a Twilio call status to (result, answered_by) for the report, or None when there is
    nothing for this endpoint to report (a human answered, so the WebSocket owns the report, or the
    status is not terminal). Answering-machine detection wins over the status: a machine, voicemail,
    fax or phone menu is `machine` however the call ends."""
    detected = (answered_by or "").lower()
    if detected.startswith("machine") or detected == "fax":
        return ("machine", "machine")
    status = (call_status or "").lower()
    if status == "no-answer":
        return ("no_answer", None)
    if status == "busy":
        return ("busy", None)
    if status in ("failed", "canceled"):
        return ("failed", None)
    return None
