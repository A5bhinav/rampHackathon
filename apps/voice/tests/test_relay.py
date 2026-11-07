"""The Twilio ConversationRelay adapter: run_relay drives the same policy.step() the text line
drives, over a WebSocket instead of a scripted vendor. These tests use an in-memory fake socket
and the ScriptedAgent, so they need no live Twilio account, phone number or model key (spec 0013).

The point is R7: a bad token, a wrong number, or a dropped socket ends the call `failed` with the
hold intact, and legitimate prompts and keypresses reach the very outcome the text line reaches."""
from __future__ import annotations

import asyncio
import uuid

from dialback_voice import tokens
from dialback_voice.agent import ScriptedAgent
from dialback_voice.transport import ScriptedVendor, VendorScript, run_call
from dialback_voice.twilio.relay import run_relay, verify_setup

KEY = b"call-token-key-for-tests-0123456789abcd"
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
CALL = uuid.UUID("6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b")
ATTEMPT = 1
NUMBER = "+14155550123"


class WSClosed(Exception):
    """The far end hung up: receive_json raises, as a real WebSocket disconnect does."""


class FakeWS:
    """A scripted ConversationRelay socket. `inbound` is the messages Twilio would send, in order;
    once they run out, receive_json raises WSClosed, standing in for a dropped call."""

    def __init__(self, inbound: list[dict]) -> None:
        self._inbound = list(inbound)
        self._i = 0
        self.sent: list[dict] = []
        self.closed = False

    async def receive_json(self) -> dict:
        if self._i >= len(self._inbound):
            raise WSClosed("no more messages")
        msg = self._inbound[self._i]
        self._i += 1
        return msg

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)

    async def close(self) -> None:
        self.closed = True


def _setup(token: str, to: str = NUMBER) -> dict:
    return {"type": "setup", "to": to, "customParameters": {"call_token": token}}


def _verify(setup: dict) -> bool:
    return verify_setup(setup, key=KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER)


def _by_value(result) -> dict[str, tuple[str, str]]:
    return {a["slot"]: (a["value"], a["capture_method"]) for a in result.answers["answers"]}


def test_bad_token_or_wrong_number_fails_closed(make_ctx, clock):
    good = tokens.mint(KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER)
    for setup, why in [(_setup("deadbeef"), "wrong token"),
                       (_setup(good, to="+19998887777"), "wrong dialed number")]:
        ws = FakeWS([setup])
        r = asyncio.run(run_relay(make_ctx(), ScriptedAgent(), ws, verify=_verify, clock=clock()))
        assert r.result == "failed", why
        assert r.answers["answers"] == [], why     # nothing recorded (R7)
        assert ws.sent == [], why                   # and nothing is ever spoken to a stray socket
        assert ws.closed is True, why


def test_ws_messages_reach_the_same_outcome_as_the_text_line(make_ctx, clock):
    # The text line's answer to a cooperative keypad call is the yardstick; the relay must match it
    # for the same callee inputs, so the two differ only in the socket, never in the decision.
    script = VendorScript(last4="4821", last4_mode="keypad")
    ctx = make_ctx()
    text = run_call(ctx, ScriptedAgent(), ScriptedVendor(script), clock=clock())

    inbound = [_setup(tokens.mint(KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER))]
    for turn in text.transcript:
        if turn.speaker != "callee":
            continue
        if turn.text == "[keypad]":
            inbound.append({"type": "dtmf", "digit": script.last4})
        else:
            inbound.append({"type": "prompt", "voicePrompt": turn.text, "last": True})

    relay = asyncio.run(run_relay(ctx, ScriptedAgent(), FakeWS(inbound), verify=_verify, clock=clock()))

    assert relay.result == text.result == "answers_recorded"
    assert _by_value(relay) == _by_value(text)       # same slots, same values, same capture method
    assert relay.checks == text.checks               # grounding, disclosure-first, no digit leak


def test_disconnect_mid_call_fails_closed(make_ctx, clock):
    # consent is recorded "yes", then the socket drops before any more is gathered. A recorded yes
    # must never turn a dropped call into a release: the call ends failed, the hold stays (R7).
    good = tokens.mint(KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER)
    inbound = [_setup(good), {"type": "prompt", "voicePrompt": "Yes, that's fine.", "last": True}]
    r = asyncio.run(run_relay(make_ctx(), ScriptedAgent(), FakeWS(inbound), verify=_verify, clock=clock()))
    assert r.result == "failed"
    assert r.end.reason == "dropped"
    by_slot = {a["slot"]: a["value"] for a in r.answers["answers"]}
    assert by_slot["recording_consent"] == "yes"     # the yes is on the books, and still fails closed


def test_the_websocket_route_runs_a_call_and_reports():
    # The /twilio/relay/{call_id} route is thin glue: accept the socket, then run_relay_call. This
    # drives a short consent-refused call through run_relay_call on a fake socket and checks it builds
    # the call from the CallDial, speaks the disclosure first, and reports the end to the core.
    import datetime as dt

    from dialback_policy.script import Script
    from dialback_voice.app import CallDial, TwilioDeps, run_relay_call

    dial = CallDial(case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER, customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",))
    token = tokens.mint(KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER)
    reported: list[dict] = []

    async def report(body: dict) -> None:
        reported.append(body)

    deps = TwilioDeps(call_token_key=KEY, twilio_auth_token=b"auth", ws_base_url="wss://voice.example",
                      public_base_url="https://voice.example",
                      lookup_call=lambda cid: dial if cid == CALL else None, report_ended=report,
                      agent_for=ScriptedAgent, clock=lambda: dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC))
    ws = FakeWS([_setup(token), {"type": "prompt", "voicePrompt": "No, I'd rather not.", "last": True}])

    asyncio.run(run_relay_call(deps, CALL, ws))

    assert ws.sent[0]["token"] == Script("Northwind Traders", "(415) 555-0100").disclosure()  # R8
    assert reported and reported[0]["result"] == "answers_recorded"
    assert reported[0]["call_id"] == str(CALL) and reported[0]["case_id"] == str(CASE)
