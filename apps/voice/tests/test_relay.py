"""The Twilio ConversationRelay adapter: run_relay drives the same policy.step() the text line
drives, over a WebSocket instead of a scripted vendor. These tests use an in-memory fake socket
and the ScriptedAgent, so they need no live Twilio account, phone number or model key (spec 0013).

The point is R7: a bad token, a wrong number, or a dropped socket ends the call `failed` with the
hold intact, and legitimate prompts and keypresses reach the very outcome the text line reaches."""
from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest
from dialback_policy import step as policy
from dialback_policy.script import QUESTIONS, REASK, Phase
from dialback_voice import tokens
from dialback_voice.agent import ScriptedAgent
from dialback_voice.transport import ScriptedVendor, VendorScript, run_call
from dialback_voice.twilio import relay as relay_module
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
    once they run out, receive_json raises WSClosed, standing in for a dropped call. An item
    `{"pause": seconds}` stands for the caller doing nothing for that long."""

    def __init__(self, inbound: list[dict]) -> None:
        self._inbound = list(inbound)
        self._i = 0
        self.sent: list[dict] = []
        self.closed = False

    async def receive_json(self) -> dict:
        while True:
            if self._i >= len(self._inbound):
                raise WSClosed("no more messages")
            msg = self._inbound[self._i]
            self._i += 1
            if "pause" not in msg:
                return msg
            await asyncio.sleep(msg["pause"])

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
                       (_setup(good, to="+19998887777"), "wrong dialed number"),
                       ({"type": "setup", "to": NUMBER}, "missing token")]:
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
            inbound += [{"type": "dtmf", "digit": d} for d in script.last4 + "#"]  # one message per key, as
        else:                                                                       # Twilio sends; '#' submits
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

    pick = uuid.UUID("7d0c1f3e-2a4b-4c5d-8e6f-9a0b1c2d3e4f")
    dial = CallDial(case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER, customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",), contact_point_id=pick)
    token = tokens.mint(KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER)
    reported: list[dict] = []

    async def report(body: dict) -> None:
        reported.append(body)

    deps = TwilioDeps(call_token_key=KEY, twilio_auth_token=b"auth", ws_base_url="wss://voice.example",
                      public_base_url="https://voice.example", from_number="+14155550100", originator=None,
                      lookup_call=lambda cid: dial if cid == CALL else None, report_ended=report,
                      agent_for=ScriptedAgent, clock=lambda: dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC))
    setup = _setup(token) | {"direction": "outbound-api"}
    setup["customParameters"]["answered_by"] = "human"   # from machine detection, via our TwiML
    ws = FakeWS([setup, {"type": "prompt", "voicePrompt": "No, I'd rather not.", "last": True}])

    asyncio.run(run_relay_call(deps, CALL, ws))

    sent = [m["token"] for m in ws.sent]
    script = Script("Northwind Traders", "(415) 555-0100")
    assert script.disclosure() not in sent              # welcomeGreeting already spoke it; not repeated (R8)
    assert sent[0] == script.consent_question()          # the first token over the socket is the consent ask
    assert reported and reported[0]["result"] == "answers_recorded"
    assert reported[0]["call_id"] == str(CALL) and reported[0]["case_id"] == str(CASE)
    assert reported[0]["contact_point_id"] == str(pick)  # spec 0030: decide() needs the pick dialed
    # Spec 0029: the facts decide() checks (V02, V03, V10, V11), from code, never the model.
    assert reported[0]["direction"] == "outbound" and reported[0]["dialed_number_is_pick"] is True
    assert reported[0]["answered_by"] == "human"
    assert set(reported[0]["checks"]) == {"disclosure_first", "answers_grounded", "agent_spoke_account_digits",
                                          "new_number_accepted", "policy_violations", "speech_guard_trips"}


def test_a_forged_setup_reports_failed_to_the_core():
    # A socket presenting a token we never minted is reported `failed` to the core (not just dropped),
    # so the case does not sit until the call_timeout. End-to-end through the route's run_relay_call.
    import datetime as dt

    from dialback_voice.app import CallDial, TwilioDeps, run_relay_call

    dial = CallDial(case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER, customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",))
    reported: list[dict] = []

    async def report(body: dict) -> None:
        reported.append(body)

    deps = TwilioDeps(call_token_key=KEY, twilio_auth_token=b"auth", ws_base_url="wss://voice.example",
                      public_base_url="https://voice.example", from_number="+14155550100", originator=None,
                      lookup_call=lambda cid: dial if cid == CALL else None, report_ended=report,
                      agent_for=ScriptedAgent, clock=lambda: dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC))
    ws = FakeWS([_setup("deadbeef")])

    asyncio.run(run_relay_call(deps, CALL, ws))

    assert ws.closed is True
    assert reported and reported[0]["result"] == "failed"
    assert "answers" not in reported[0]                  # a rejected setup records nothing
    for fact in ("dialed_number_is_pick", "direction", "answered_by", "checks"):
        assert fact not in reported[0], fact             # nothing vouched for on a socket we didn't place


# --- spec 0043: the keys, and one more try on an unclear answer ------------------------------

START_OVER_LINE = "Okay, let's start over. Please enter the last four digits, then press the pound key."
SPOKEN = ("Yes, that's fine.", "Acme Fasteners", "Accounts payable", "Jordan Lee", "AP clerk", "Yes, we did.")
BEFORE_DIGITS = ["recording_consent", "company_named", "department", "speaker_name", "speaker_role",
                 "change_requested"]
FAST = 0.2   # the keypad wait these tests run with, in seconds; a live call waits KEYPAD_TIMEOUT
LONG = 2.0   # a pause well past FAST


def _good_setup() -> dict:
    return _setup(tokens.mint(KEY, case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER))


def _say(*lines: str) -> list[dict]:
    return [{"type": "prompt", "voicePrompt": line, "last": True} for line in lines]


def _keys(keys: str) -> list[dict]:
    return [{"type": "dtmf", "digit": k} for k in keys]  # one message per key, as Twilio sends


def _spy_on_keypad(monkeypatch) -> list[str]:
    """The digits of each Keypad event the relay hands to step, kept only inside the test."""
    seen: list[str] = []
    real = policy.step

    def spy(state, event):
        if isinstance(event, policy.Keypad):
            seen.append(event.digits)
        return real(state, event)

    monkeypatch.setattr(policy, "step", spy)
    return seen


def _relay(ctx, clock, inbound: list[dict], agent=None):
    ws = FakeWS(inbound)
    result = asyncio.run(run_relay(ctx, agent or ScriptedAgent(), ws, verify=_verify, clock=clock(),
                                   keypad_timeout=FAST))
    return result, [m["token"] for m in ws.sent]


# The keys typed after the digits question; then what is recorded, how many re-asks and start-over
# lines play, how the call ends, and the entries step is handed. A row that ends on a re-ask then
# runs out of messages, which is a dropped call (failed).
KEY_TABLE = {
    "4 8 2 1 #": (_keys("4821#"), "4821", 0, 0, "answers_recorded", ["4821"]),
    "4 8 * 4 8 2 1 #": (_keys("48*4821#"), "4821", 0, 1, "answers_recorded", ["4821"]),
    "4 8 2 1, then no key": (_keys("4821") + [{"pause": LONG}], "4821", 0, 0, "answers_recorded", ["4821"]),
    "4 8 #": (_keys("48#"), None, 1, 0, "failed", ["48"]),
    "4 8 # then 4 8 #": (_keys("48#48#"), None, 1, 0, "answers_recorded", ["48", "48"]),
    "4 8 2 1 5 #": (_keys("48215#"), None, 1, 0, "failed", ["48215"]),
    "20 digits": (_keys("12345678901234567890#"), None, 1, 0, "failed", ["1234567890123456"]),
}


@pytest.mark.parametrize("row", list(KEY_TABLE))
def test_keypad_sequences(row, make_ctx, clock, monkeypatch):
    keys, recorded, reasks, start_overs, result, entries = KEY_TABLE[row]
    seen = _spy_on_keypad(monkeypatch)
    ctx = make_ctx()
    r, sent = _relay(ctx, clock, [_good_setup(), *_say(*SPOKEN), *keys])

    assert r.result == result
    assert seen == entries                               # no auto-submit at four; at most 16 keys held
    slots = [a["slot"] for a in r.answers["answers"]]
    if recorded is None:
        assert slots == BEFORE_DIGITS                    # nothing recorded for the digits
    else:
        assert slots == [*BEFORE_DIGITS, "account_last4"]
        assert _by_value(r)["account_last4"] == (recorded, "keypad")
    reask = f"{REASK} {QUESTIONS[Phase.LAST4].wording(ctx.script)}"
    assert sum(line.startswith(REASK) for line in sent) == reasks
    assert sent.count(reask) == reasks                   # the re-ask is the fixed keypad question
    assert sent.count(START_OVER_LINE) == start_overs
    # Typed digits never appear in any transcript turn, and the agent never says them.
    assert not any(ch.isdigit() for t in r.transcript if t.speaker == "callee" for ch in t.text)
    assert not any(e in t.text for t in r.transcript for e in seen if len(e) >= 3)
    assert r.checks["agent_spoke_account_digits"] is False


def test_keypad_timeout_submits(make_ctx, clock, monkeypatch):
    # A live call waits 5 seconds after the last key (Q78).
    assert relay_module.KEYPAD_TIMEOUT == 5
    assert inspect.signature(run_relay).parameters["keypad_timeout"].default == relay_module.KEYPAD_TIMEOUT
    seen = _spy_on_keypad(monkeypatch)
    # With no digits in hand a long pause submits nothing; a pause shorter than the wait keeps one
    # entry going; a pause past the wait submits what is typed as if '#' were pressed, whatever its
    # length (three digits here, so only the wait can have submitted it).
    inbound = [_good_setup(), {"pause": 3 * FAST}, *_say(*SPOKEN), *_keys("48"), {"pause": FAST / 10},
               *_keys("2"), {"pause": 3 * FAST}, *_keys("4821"), {"pause": LONG}]
    r, sent = _relay(make_ctx(), clock, inbound)
    assert seen == ["482", "4821"]
    assert sum(line.startswith(REASK) for line in sent) == 1   # the short entry used the one more try
    assert r.result == "answers_recorded" and _by_value(r)["account_last4"] == ("4821", "keypad")


@pytest.mark.parametrize("between", [_say("Hmm."), [{"type": "prompt", "voicePrompt": "", "last": True}]],
                         ids=["spoken turn", "silence"])
def test_a_spoken_turn_or_a_silence_clears_a_partial_entry(between, make_ctx, clock, monkeypatch):
    seen = _spy_on_keypad(monkeypatch)
    r, _ = _relay(make_ctx(), clock, [_good_setup(), *_say(*SPOKEN), *_keys("48"), *between, *_keys("4821#")])
    assert seen == ["4821"]                              # the "48" before the turn was dropped, as today
    assert _by_value(r)["account_last4"] == ("4821", "keypad")


def test_unclear_consent_is_asked_again_over_the_relay(make_ctx, clock):
    ctx = make_ctx()
    r, sent = _relay(ctx, clock, [_good_setup(), *_say("Hello?", *SPOKEN), *_keys("4821#")])
    assert f"{REASK} {ctx.script.consent_question()}" in sent
    assert r.result == "answers_recorded" and _by_value(r)["recording_consent"] == ("yes", "speech")
    assert r.checks["answers_grounded"] is True


@pytest.mark.parametrize(("before", "unclear", "reason"), [
    ((), "Hello?", "unclear:consent"),
    (SPOKEN[:5], "Sorry?", "unclear:change"),
])
def test_a_second_unclear_reports_failed_without_answers(before, unclear, reason, make_ctx, clock):
    import datetime as dt

    from dialback_voice.app import CallDial, TwilioDeps, run_relay_call

    dial = CallDial(case_id=CASE, attempt=ATTEMPT, number_e164=NUMBER, customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",))
    reported: list[dict] = []

    async def report(body: dict) -> None:
        reported.append(body)

    deps = TwilioDeps(call_token_key=KEY, twilio_auth_token=b"auth", ws_base_url="wss://voice.example",
                      public_base_url="https://voice.example", from_number="+14155550100", originator=None,
                      lookup_call=lambda cid: dial if cid == CALL else None, report_ended=report,
                      agent_for=ScriptedAgent, clock=lambda: dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC))
    ws = FakeWS([_good_setup(), *_say(*before, unclear, unclear)])
    asyncio.run(run_relay_call(deps, CALL, ws))

    assert len(reported) == 1 and reported[0]["result"] == "failed"
    assert "answers" not in reported[0] and "retry_at" not in reported[0]   # the core redials after its wait
    assert sum(m["token"].startswith(REASK) for m in ws.sent) == 1
    # The reason stays inside the voice service (End.reason); the same run names it.
    again, _ = _relay(make_ctx(), clock, [_good_setup(), *_say(*before, unclear, unclear)])
    assert (again.result, again.end.reason, again.end.retry_at) == ("failed", reason, None)
