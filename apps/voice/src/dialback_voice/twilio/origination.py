"""Starting a Twilio ConversationRelay call, and trusting Twilio's webhooks.

`build_connect_twiml` writes the TwiML the voice service returns when Twilio fetches a call's
instructions: `<Connect><ConversationRelay>` with the fixed disclosure as the welcomeGreeting (so it
plays first, R8), the vendor's names as speech `hints` (spec 0082), and the one-time call_token as a
`<Parameter>`. The case id and the dialed number's lineage are never written into it.
`valid_twilio_signature` checks the X-Twilio-Signature that Twilio puts on its webhook requests, so a
forged POST moves nothing (R9). `status_result` turns a call that never reached a live conversation
into the result the core is told.

The REST call that actually dials (passing this TwiML's URL and the status-callback URL) is the
live piece, done by the ops-provisioned Twilio client; it is not in this module because it cannot
run or be tested without a live account (spec 0013, Q47/Q48)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Protocol
from xml.sax.saxutils import quoteattr

import httpx
from dialback_policy.guard import distinctive_first_word, words

MAX_HINTS = 10
MAX_HINT_CHARS = 100

# Twilio's answering-machine detection settings, sent on every dial (spec 0080, Part A). Detection
# stays synchronous: Twilio labels the call before it fetches our TwiML, so the disclosure starts
# only after the person stops talking, or after silence (R8). The errors are lopsided: a false
# "machine" sends a good call to review, while a false "human" on a voicemail costs almost nothing,
# because a voicemail cannot key the four digits. So the threshold leans toward "human". The
# values are educated guesses until the first live calls (docs/live-call.md, section 5).

# Talking for longer than this is labeled a machine. Twilio's default is 2400 ms and its guide
# suggests 1800 to 3000 ms for businesses; 4500 ms lets a business greeting of up to about 4 s
# finish and be labeled human, at the cost of some voicemails of 3 to 4.5 s labeled human too.
AMD_SPEECH_THRESHOLD_MS = 4500
# Quiet after talking that counts as "they finished": the dead air after "Hello?". Twilio's default,
# sent explicitly. Lower would save a little dead air but end a greeting at a natural pause, so
# the disclosure would start while the person is still talking, on a call that can verify.
AMD_SPEECH_END_THRESHOLD_MS = 1200
# Silence after pickup before Twilio gives up with "unknown": half Twilio's 5000 ms default (spec 0080,
# Part C), so someone who picks up and waits hears the disclosure after about 2.5 s. More calls come
# back "unknown", which no longer blocks: with the digits typed on the keypad they verify (Part B).
AMD_SILENCE_TIMEOUT_MS = 2500


class Originator(Protocol):
    """The seam that actually dials. `run_relay` and the routes are pure of Twilio's REST API; the
    handler asks this to place the call and gets back the CallSid. The tests pass a fake, so the
    dial is exercised with no live account; `TwilioOriginator` is the real client."""
    async def originate(self, *, to: str, from_: str, twiml_url: str, status_url: str) -> str: ...


class TwilioOriginator:
    """Places a ConversationRelay call through Twilio's REST API. `url` is the TwiML endpoint Twilio
    fetches for the call's instructions, `status_url` is where terminal status and answering-machine
    detection are POSTed back. Machine detection is on so `status_result` can map voicemail to
    `machine`. A non-2xx raises (httpx `raise_for_status`), which the caller turns into a fail-closed
    `failed` (R7). Only the pick, the caller id and our own URLs are sent: never the case id."""

    _API = "https://api.twilio.com/2010-04-01"

    def __init__(self, http: httpx.AsyncClient, *, account_sid: str, auth_token: bytes) -> None:
        self._http = http
        self._sid = account_sid
        self._auth = (account_sid, auth_token.decode())

    async def originate(self, *, to: str, from_: str, twiml_url: str, status_url: str) -> str:
        resp = await self._http.post(
            f"{self._API}/Accounts/{self._sid}/Calls.json",
            data={"To": to, "From": from_, "Url": twiml_url,
                  "StatusCallback": status_url, "StatusCallbackEvent": "completed",
                  "MachineDetection": "Enable",
                  "MachineDetectionSpeechThreshold": str(AMD_SPEECH_THRESHOLD_MS),
                  "MachineDetectionSpeechEndThreshold": str(AMD_SPEECH_END_THRESHOLD_MS),
                  "MachineDetectionSilenceTimeout": str(AMD_SILENCE_TIMEOUT_MS)},
            auth=self._auth)
        resp.raise_for_status()
        return resp.json()["sid"]


def _bare(word: str) -> str:
    """`word` without the punctuation and symbols around it ('"Acme",' is 'Acme')."""
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start])[0] in "PS":
        start += 1
    while end > start and unicodedata.category(word[end - 1])[0] in "PS":
        end -= 1
    return word[start:end]


def _first_word(name: str) -> str | None:
    """The word of `name`, as written, that the speech guard would accept on its own, or None. The
    guard skips "The" and legal suffixes, so "The Acme Group" gives "Acme". A word that reads as more
    than that one token ("Acme-Fasteners") is not a first word on its own, so it gives None."""
    first = distinctive_first_word(name)
    if first is None:
        return None
    for word in name.split():
        bare = _bare(word)
        tokens = words(bare).split()
        if first in tokens:
            return bare if tokens == [first] else None
    return None


def speech_hints(vendor_names: Iterable[str]) -> list[str]:
    """The words the recognizer should listen for (spec 0082, D2): each vendor name as written, then
    its first word when the speech guard would accept that word on its own. Control characters are
    dropped and commas become spaces, so a name cannot break the TwiML or split into two hints.
    Empty hints, hints over 100 characters and duplicates (ignoring case) are skipped; at most 10."""
    hints: list[str] = []
    seen: set[str] = set()
    for raw in vendor_names:
        visible = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C")
        name = " ".join(visible.replace(",", " ").split())
        for hint in (name, _first_word(name)):
            if hint and len(hint) <= MAX_HINT_CHARS and hint.casefold() not in seen:
                seen.add(hint.casefold())
                hints.append(hint)
    return hints[:MAX_HINTS]


def build_connect_twiml(*, ws_url: str, call_token: str, disclosure: str, answered_by: str | None = None,
                        vendor_names: Iterable[str] = ()) -> str:
    """The TwiML that connects the call to our ConversationRelay socket. `quoteattr` escapes every
    value, so the disclosure's punctuation cannot break out of its attribute. The vendor's names go
    only into `hints`, which biases the speech recognizer and is never spoken (spec 0082, R4)."""
    # interruptible="false" keeps the disclosure from being cut off (R8). Whether the greeting alone
    # can be non-interruptible while the rest of the call is interruptible is Q45; until that is
    # confirmed the whole call is non-interruptible, which is the safe side.
    # reportInputDuringAgentSpeech="dtmf": keys pressed while the agent is still speaking reach the
    # relay (spec 0043). Twilio's default has been "none" since May 2025, which drops them, and
    # callers often start typing the last four before the question ends. Keys still don't cut the
    # agent off, and speech during the agent's turn is still not reported.
    # events="speaker-events": Twilio sends agentSpeaking and clientSpeaking, so the relay's quiet
    # clock starts when the agent stops and is postponed while the caller speaks (spec 0051, D3; the
    # exact message is Q117, and the relay falls back to an estimate without it).
    attrs = (f"url={quoteattr(ws_url)} welcomeGreeting={quoteattr(disclosure)} "
             f'dtmfDetection="true" interruptible="false" reportInputDuringAgentSpeech="dtmf" '
             'events="speaker-events"')
    # hints: one list for the whole call (Twilio documents it per session, not per question), and
    # left out when empty so a call with no vendor name gets exactly the TwiML it got before.
    hints = speech_hints(vendor_names)
    if hints:
        attrs += f" hints={quoteattr(','.join(hints))}"
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f"<ConversationRelay {attrs}>"
            f"<Parameter name=\"call_token\" value={quoteattr(call_token)}/>"
            + (f"<Parameter name=\"answered_by\" value={quoteattr(answered_by)}/>" if answered_by else "")
            + "</ConversationRelay>"
            "</Connect></Response>")


def valid_twilio_signature(auth_token: bytes, signature: str, url: str,
                           params: Mapping[str, str] | Iterable[tuple[str, str]]) -> bool:
    """True if `signature` is Twilio's X-Twilio-Signature for this request: HMAC-SHA1, keyed by the
    account's auth token, over the full URL Twilio requested (query string included) followed by
    every POST field, blank values included, as key+value sorted by key then value with duplicates
    removed, base64-encoded (Twilio's published scheme; spec 0034, D3). `params` is the form's
    (key, value) pairs, or a mapping. Constant-time, so a wrong signature leaks nothing."""
    pairs = params.items() if isinstance(params, Mapping) else params
    data = url + "".join(key + value for key, value in sorted(set(pairs)))
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
