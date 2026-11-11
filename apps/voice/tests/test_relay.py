"""The Twilio ConversationRelay adapter: run_relay drives the same policy.step() the text line
drives, over a WebSocket instead of a scripted vendor. These tests use an in-memory fake socket
and the ScriptedAgent, so they need no live Twilio account, phone number or model key (spec 0013).

The point is R7: a bad token, a wrong number, or a dropped socket ends the call `failed` with the
hold intact, and legitimate prompts and keypresses reach the very outcome the text line reaches."""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid

import pytest
from dialback_policy import step as policy
from dialback_policy.script import QUESTIONS, REASK, Phase
from dialback_policy.tools import ToolCall
from dialback_voice import tokens
from dialback_voice.agent import AgentReply, ScriptedAgent, last_callee
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
    `{"pause": seconds}` stands for the caller doing nothing for that long. `delivered_at` is the
    monotonic time each message was handed over, and `sent_at` the time each line was sent."""

    def __init__(self, inbound: list) -> None:
        self._inbound = list(inbound)
        self._i = 0
        self.sent: list[dict] = []
        self.sent_at: list[float] = []
        self.closed = False
        self.delivered_at: list[float] = []

    async def receive_json(self) -> dict:
        while True:
            if self._i >= len(self._inbound):
                raise WSClosed("no more messages")
            msg = self._inbound[self._i]
            self._i += 1
            if not (isinstance(msg, dict) and "pause" in msg):
                self.delivered_at.append(time.monotonic())
                return msg
            await asyncio.sleep(msg["pause"])

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)
        self.sent_at.append(time.monotonic())

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


def _spy_on_keypad(monkeypatch, at: list[float] | None = None) -> list[str]:
    """The digits of each Keypad event the relay hands to step, kept only inside the test, and, in
    `at`, the monotonic time each one arrived."""
    seen: list[str] = []
    real = policy.step

    def spy(state, event):
        if isinstance(event, policy.Keypad):
            seen.append(event.digits)
            if at is not None:
                at.append(time.monotonic())
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
    # A live call waits 10 seconds after the last key (Q78, revised from 5 by Sami on Nov 9: a caller
    # may pause mid-entry to find the rest of the digits).
    assert relay_module.KEYPAD_TIMEOUT == 10
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


@pytest.mark.parametrize(("between", "after"), [(_say("Hmm."), "4821#"),
                                                ([{"type": "prompt", "voicePrompt": "", "last": True}], "21#")],
                         ids=["spoken turn", "empty final prompt"])
def test_a_spoken_turn_clears_a_partial_entry_and_line_noise_does_not(between, after, make_ctx, clock,
                                                                      monkeypatch):
    # A spoken turn drops the "48" typed before it, as before. An empty final transcription is line
    # noise: activity for the quiet clock, never a Silence (spec 0051, D2; it was a Silence before, and
    # cleared the entry), so the entry carries on across it.
    seen = _spy_on_keypad(monkeypatch)
    r, _ = _relay(make_ctx(), clock, [_good_setup(), *_say(*SPOKEN), *_keys("48"), *between, *_keys(after)])
    assert seen == ["4821"]
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


def test_keys_past_the_cap_do_not_hold_the_entry_open(make_ctx, clock, monkeypatch):
    # D2, bounded: a digit dropped past MAX_KEYS does not restart the wait, so the cap bounds how long
    # an entry stays open as well as its size. The keys after the 16th come sooner than the wait,
    # for three waits' worth of time.
    at: list[float] = []
    seen = _spy_on_keypad(monkeypatch, at)
    more = [msg for _ in range(12) for msg in ({"pause": FAST / 4}, *_keys("9"))]
    ws = FakeWS([_good_setup(), *_say(*SPOKEN), *_keys("1234567890123456"), *more, {"pause": LONG}])
    asyncio.run(run_relay(make_ctx(), ScriptedAgent(), ws, verify=_verify, clock=clock(), keypad_timeout=FAST))
    sixteenth = ws.delivered_at[1 + len(SPOKEN) + 15]    # after the setup and the spoken answers
    assert seen[0] == "1234567890123456"
    assert 0.8 * FAST <= at[0] - sixteenth < 2 * FAST    # about one wait after the 16th key, not the last
    assert len(seen) == 2 and set(seen[1]) == {"9"}      # keys after the entry went in start a new one


def test_the_wait_restarts_on_every_kept_key(make_ctx, clock, monkeypatch):
    # Each gap is shorter than the wait and the whole entry is longer than it: still one entry.
    seen = _spy_on_keypad(monkeypatch)
    gap = {"pause": 0.6 * FAST}
    keys = [*_keys("4"), gap, *_keys("8"), gap, *_keys("2"), gap, *_keys("1#")]
    r, _ = _relay(make_ctx(), clock, [_good_setup(), *_say(*SPOKEN), *keys])
    assert seen == ["4821"]
    assert _by_value(r)["account_last4"] == ("4821", "keypad")


def test_two_short_entries_finish_without_digits_and_decide_gives_nr06(make_ctx, clock):
    from dialback_decision import decide
    from dialback_decision.facts import CallChecks, facts_from_answers
    from dialback_decision.last_four import compare_last_four

    r, _ = _relay(make_ctx(), clock, [_good_setup(), *_say(*SPOKEN), *_keys("48#48#")])
    answers = r.answers["answers"]
    typed = [a["value"] for a in answers if a["slot"] == "account_last4"]
    assert r.result == "answers_recorded" and typed == []
    # The facts the core adds from outside the call, declared as a clean call has them; the last four
    # are compared the way the core compares them.
    facts = facts_from_answers(answers, call_result=r.result, checks=CallChecks(**r.checks), dialed_out=True,
                               dialed_eligible_number=True, dialed_number_is_pick=True,
                               last_four_result=compare_last_four(typed, "4821", "4444"), company_matches=True,
                               answered_by="human", speaker_in_change_thread=False)
    decision = decide(facts)
    assert decision.outcome == "needs_review" and "NR06_digits_not_provided" in decision.rules_fired


# --- spec 0051: the quiet clock ---------------------------------------------------------------

Q = 0.3      # the quiet wait these tests run with; a live call waits QUIET_SECONDS
Q4 = 0.6     # the same at the digits question (QUIET_SECONDS_LAST4)
A = 0.3      # after a check-in (AFTER_CHECK_IN_SECONDS)
A4 = 0.6     # after the digits check-in (AFTER_CHECK_IN_SECONDS_LAST4)
SLACK = 0.8  # how late a timer may run on a loaded CI runner; nothing may come early
STILL_THERE_LINE = "Sorry, are you still there?"
DIGITS_CHECK_IN = ("Take your time. When you have the last four digits, enter them on your keypad, then press the "
                   "pound key.")


def _info(name, value) -> dict:
    return {"type": "info", "name": name, "value": value}


def _estimate(lead: float = 0.0, chars_per_second: float = 1e9, margin: float = 0.0):
    """The speech estimate; by default about zero, so the clock starts as the last line is sent."""
    return relay_module.SpeechEstimate(lead=lead, chars_per_second=chars_per_second, margin=margin)


def _spy_on_events(monkeypatch) -> list[tuple[str, float]]:
    """Each event the relay hands to step, by type, with the monotonic time it arrived."""
    seen: list[tuple[str, float]] = []
    real = policy.step

    def spy(state, event):
        seen.append((type(event).__name__, time.monotonic()))
        return real(state, event)

    monkeypatch.setattr(policy, "step", spy)
    return seen


def _silences(events: list[tuple[str, float]]) -> list[float]:
    return [at for name, at in events if name == "Silence"]


def _quiet_relay(ctx, clock, inbound: list, estimate=None, agent=None, **timings):
    """Run a call with the short test timings (`timings` overrides any of them). The welcome greeting
    speaks the disclosure, as on a live call, so the first line sent is the consent question."""
    ws = FakeWS(inbound)
    kw = {"keypad_timeout": FAST, "quiet_seconds": Q, "quiet_seconds_last4": Q4, "after_check_in_seconds": A,
          "after_check_in_seconds_last4": A4} | timings
    result = asyncio.run(run_relay(ctx, agent or ScriptedAgent(), ws, verify=_verify, clock=clock(),
                                   greeting=ctx.script.disclosure(), estimate=estimate or _estimate(), **kw))
    return result, ws


def _about(waited: float, expected: float) -> bool:
    """`waited` is `expected`, never early, and late only by scheduling."""
    return 0.95 * expected <= waited < expected + SLACK


def test_quiet_clock_timings_are_the_spec_values():  # D1, D3
    assert (relay_module.QUIET_SECONDS, relay_module.QUIET_SECONDS_LAST4, relay_module.AFTER_CHECK_IN_SECONDS,
            relay_module.AFTER_CHECK_IN_SECONDS_LAST4) == (10, 20, 10, 20)
    params = inspect.signature(run_relay).parameters
    assert params["quiet_seconds"].default == relay_module.QUIET_SECONDS
    assert params["quiet_seconds_last4"].default == relay_module.QUIET_SECONDS_LAST4
    assert params["after_check_in_seconds"].default == relay_module.AFTER_CHECK_IN_SECONDS
    assert params["after_check_in_seconds_last4"].default == relay_module.AFTER_CHECK_IN_SECONDS_LAST4
    # 1 second, plus 1 second per 12 characters of every line, plus 3 seconds of margin.
    assert params["estimate"].default.seconds(("x" * 24, "y" * 12)) == 1 + 3 + 3


def test_quiet_clock_fires_a_silence_after_an_agent_line(make_ctx, clock, monkeypatch):  # C1
    events = _spy_on_events(monkeypatch)
    ctx = make_ctx()
    r, ws = _quiet_relay(ctx, clock, [_good_setup(), {"pause": 3 * LONG}])
    silences = _silences(events)
    sent = [m["token"] for m in ws.sent]
    assert sent == [ctx.script.consent_question(), STILL_THERE_LINE, ctx.script.goodbye()]
    assert len(silences) == 2
    assert _about(silences[0] - ws.sent_at[0], Q)        # the quiet wait after the question
    assert _about(silences[1] - ws.sent_at[1], A)        # the wait after the check-in
    assert (r.result, r.end.reason) == ("failed", "silence") and r.answers["answers"] == []


ACTIVITY = {
    "partial prompt": [{"type": "prompt", "voicePrompt": "Um", "last": False}],
    "empty final prompt": [{"type": "prompt", "voicePrompt": "", "last": True}],
    "key": [{"type": "dtmf", "digit": "1"}],
    "clientSpeaking": [_info("clientSpeaking", "on"), _info("clientSpeaking", "off")],   # a short utterance
}


@pytest.mark.parametrize("kind", list(ACTIVITY))
def test_quiet_clock_activity_postpones_it(kind, make_ctx, clock, monkeypatch):  # C1, D2
    # Two of the same activity, 0.4 of the wait apart: the Silence comes a full wait after the second,
    # not after the line nor after the first. Without the postponement it would come 0.2 of the wait
    # after the second at the latest, so the "never early" bound fails it.
    events = _spy_on_events(monkeypatch)
    gap = {"pause": 0.4 * Q}
    r, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), gap, *ACTIVITY[kind], gap, *ACTIVITY[kind],
                                             {"pause": 3 * LONG}])
    latest = ws.delivered_at[-1]
    silences = _silences(events)
    held = FAST if kind == "key" else 0.0   # keys are held for the keypad wait; then the clock starts at once
    assert silences and _about(silences[0] - latest, Q + held)
    assert ws.sent[1]["token"] == STILL_THERE_LINE
    assert (r.result, r.end.reason) == ("failed", "silence")


@pytest.mark.parametrize("kind", ["lone clientSpeaking", "key at another question"])
def test_quiet_clock_still_fires_after_a_lone_client_speaking_or_a_key_elsewhere(kind, make_ctx, clock,
                                                                                 monkeypatch):  # C1, D2, D3
    events = _spy_on_events(monkeypatch)
    if kind == "lone clientSpeaking":
        inbound = [_good_setup(), _info("clientSpeaking", "on"), {"pause": 3 * LONG}]
    else:   # at the company question; the key means nothing there and says nothing
        inbound = [_good_setup(), *_say("Yes, that's fine."), *_keys("1"), {"pause": 3 * LONG}]
    r, ws = _quiet_relay(make_ctx(), clock, inbound)
    silences = _silences(events)
    assert len(silences) == 2 and (r.result, r.end.reason) == ("failed", "silence")
    if kind == "lone clientSpeaking":   # held as mid-sentence, until the per-wait cap (review fixes 2, 3)
        assert _about(silences[0] - ws.sent_at[0], 3 * Q)
    if kind == "key at another question":
        keypad = [at for name, at in events if name == "Keypad"]
        assert len(keypad) == 1
        assert _about(silences[0] - keypad[0], Q)      # a step that said nothing: the clock starts at once
        assert ws.sent[-2]["token"] == STILL_THERE_LINE


def test_quiet_clock_starts_at_the_estimate_counting_the_disclosure(make_ctx, clock, monkeypatch):  # C1, D3
    events = _spy_on_events(monkeypatch)
    ctx = make_ctx()
    lead, per_second, margin = 0.1, 500.0, 0.1
    _, ws = _quiet_relay(ctx, clock, [_good_setup(), {"pause": 3 * LONG}],
                         estimate=_estimate(lead, per_second, margin))
    assert ws.sent[0]["token"] == ctx.script.consent_question()   # the disclosure was the welcome greeting
    spoken = len(ctx.script.disclosure()) + len(ctx.script.consent_question())
    silences = _silences(events)
    # From the moment the consent question was sent, the estimate counts the disclosure too.
    assert _about(silences[0] - ws.sent_at[0], lead + spoken / per_second + margin + Q)


def test_quiet_clock_starts_when_the_agent_stops_speaking(make_ctx, clock, monkeypatch):  # C1, D3
    on, off = _info("agentSpeaking", "on"), _info("agentSpeaking", "off")
    slow = _estimate(lead=1.5)                            # the estimate alone would start the clock much later
    # With an "on" then an "off" after the line was sent, the clock starts at the "off".
    events = _spy_on_events(monkeypatch)
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), {"pause": 0.1}, on, {"pause": 0.2}, off,
                                             {"pause": 1.0}], estimate=slow)
    stopped = ws.delivered_at[2]
    silences = _silences(events)
    assert silences and _about(silences[0] - stopped, Q)
    # A lone "off", with no "on" since the line was sent, does not start it: the estimate does.
    events.clear()
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), {"pause": 0.05}, off, {"pause": 3 * LONG}],
                         estimate=_estimate(lead=0.6))
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[0], 0.6 + Q)
    # Nor does an "off" whose "on" came before the line was sent (one that piled up while the model
    # answered): the clock for the company question starts at its estimate.
    events.clear()
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), on, *_say("Yes, that's fine."), off,
                                             {"pause": 3 * LONG}], estimate=_estimate(lead=0.6))
    company = [m["token"] for m in ws.sent].index(QUESTIONS[Phase.COMPANY].wording(make_ctx().script))
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[company], 0.6 + Q)


def test_quiet_clock_uses_the_last4_timings(make_ctx, clock, monkeypatch):  # C1, C4, D1, D6
    from dialback_decision import decide
    from dialback_decision.facts import CallChecks, facts_from_answers
    from dialback_decision.last_four import compare_last_four

    events = _spy_on_events(monkeypatch)
    ctx = make_ctx()
    r, ws = _quiet_relay(ctx, clock, [_good_setup(), *_say(*SPOKEN), {"pause": 3 * LONG}])
    sent = [m["token"] for m in ws.sent]
    asked = sent.index(QUESTIONS[Phase.LAST4].wording(ctx.script))
    assert sent[asked + 1:] == [DIGITS_CHECK_IN, ctx.script.goodbye()]
    silences = _silences(events)
    assert len(silences) == 2
    assert _about(silences[0] - ws.sent_at[asked], Q4)
    assert _about(silences[1] - ws.sent_at[asked + 1], A4)
    # No reply at the digits question: the call finishes with every other answer and no digits, so
    # decide() gives NR06 and a person sees the full call.
    answers = r.answers["answers"]
    assert r.result == "answers_recorded" and [a["slot"] for a in answers] == BEFORE_DIGITS
    facts = facts_from_answers(answers, call_result=r.result, checks=CallChecks(**r.checks), dialed_out=True,
                               dialed_eligible_number=True, dialed_number_is_pick=True,
                               last_four_result=compare_last_four([], "4821", "4444"), company_matches=True,
                               answered_by="human", speaker_in_change_thread=False)
    decision = decide(facts)
    assert decision.outcome == "needs_review" and "NR06_digits_not_provided" in decision.rules_fired


def test_unknown_info_message_is_ignored(make_ctx, clock, monkeypatch):  # C6
    junk = [{"type": "info"}, _info("tokensPlayed", "x"), _info("clientSpeaking", None), _info("clientSpeaking", 1),
            _info(["clientSpeaking"], "on"), _info("agentSpeaking", "maybe"), _info("agentSpeaking", None),
            {"type": "mystery"}, ["not", "an", "object"], "text"]
    # None of it is activity, so it does not postpone the clock. A longer wait here, so a postponed
    # Silence (about 1.8 s after the line) and a right one (1 s) are far apart even on a slow runner.
    events = _spy_on_events(monkeypatch)
    wait = 1.0
    r, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), {"pause": 0.8 * wait}, *junk, {"pause": 3 * LONG}],
                         quiet_seconds=wait)
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[0], wait)
    assert silences[0] < ws.delivered_at[-1] + 0.9 * wait
    assert (r.result, r.end.reason) == ("failed", "silence")
    # ... and no malformed agentSpeaking starts it before the estimate.
    events.clear()
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), _info("agentSpeaking", "on"), *junk,
                                             {"pause": 3 * LONG}], estimate=_estimate(lead=0.6))
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[0], 0.6 + Q)


def test_quiet_clock_a_socket_error_while_waiting_is_a_hangup(make_ctx, clock, monkeypatch):  # C6
    events = _spy_on_events(monkeypatch)
    r, _ = _quiet_relay(make_ctx(), clock, [_good_setup(), *_say("Yes, that's fine."), {"pause": 0.5 * Q}])
    names = [name for name, _ in events]
    assert "Silence" not in names and names[-1] == "Hangup"
    assert (r.result, r.end.reason) == ("failed", "dropped")


# --- spec 0051, the review's fixes --------------------------------------------------------------

CALLBACK_TIME_QUESTION = "What time would work best for us to call you back?"


class _CallbackAgent(ScriptedAgent):
    """The double, except that a request to be called back schedules a callback with no time, so the
    callback-time question is asked."""

    def reply(self, request):
        _, text = last_callee(request.transcript)
        if "call me back" in text.lower():
            return AgentReply(tool_calls=(ToolCall(id="tc_callback", name="schedule_callback",
                                                   args={"window": None}),))
        return super().reply(request)


def test_quiet_clock_follows_the_open_question(make_ctx, clock, monkeypatch):  # fix 1
    # The callback-time question asked at the digits question is the open one: the plain check-in,
    # the plain timings, and no reply ends failed. The digits timings are long here, so using them
    # would show.
    events = _spy_on_events(monkeypatch)
    ctx = make_ctx()
    r, ws = _quiet_relay(ctx, clock, [_good_setup(), *_say(*SPOKEN, "Can you call me back later?"),
                                      {"pause": 3 * LONG}], agent=_CallbackAgent(),
                         quiet_seconds_last4=3.0, after_check_in_seconds_last4=3.0)
    sent = [m["token"] for m in ws.sent]
    asked = sent.index(CALLBACK_TIME_QUESTION)
    assert sent[asked + 1:] == [STILL_THERE_LINE, ctx.script.goodbye()]
    silences = _silences(events)
    assert len(silences) == 2
    assert _about(silences[0] - ws.sent_at[asked], Q) and _about(silences[1] - ws.sent_at[asked + 1], A)
    assert (r.result, r.end.reason) == ("failed", "silence")


def test_quiet_clock_has_a_hard_cap_per_wait(make_ctx, clock, monkeypatch):  # fix 2
    # Activity that never stops (a partial transcription every 0.1 s) cannot postpone the clock
    # forever: the Silence comes QUIET_CAP_FACTOR waits after the line, while it is still going on.
    events = _spy_on_events(monkeypatch)
    babble = [msg for _ in range(40) for msg in ({"pause": 0.1}, {"type": "prompt", "voicePrompt": "Um",
                                                                  "last": False})]
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), *babble, {"pause": 3 * LONG}])
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[0], 3 * Q)
    assert silences[0] < ws.delivered_at[-1]
    assert relay_module.QUIET_CAP_FACTOR == 3


def test_quiet_clock_holds_while_the_caller_is_speaking(make_ctx, clock, monkeypatch):  # fix 3
    # A clientSpeaking "on" with no "off" yet is a caller mid-sentence: no Silence until the "off"
    # (it would come a wait after the "on" without the hold), then a full wait from it. The cap still
    # applies: see the lone clientSpeaking case above.
    events = _spy_on_events(monkeypatch)
    wait = 0.5
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), {"pause": 0.05}, _info("clientSpeaking", "on"),
                                             {"pause": 1.2 * wait}, _info("clientSpeaking", "off"),
                                             {"pause": 3 * LONG}], quiet_seconds=wait)
    stopped = ws.delivered_at[-1]
    silences = _silences(events)
    assert silences and _about(silences[0] - stopped, wait)


def test_a_json_null_is_ignored_not_a_timeout(make_ctx, clock, monkeypatch):  # fix 4
    # A null frame is a malformed message, not the receive timing out: it neither submits held
    # digits nor brings the Silence early.
    entries = _spy_on_keypad(monkeypatch)
    r, _ = _quiet_relay(make_ctx(), clock, [_good_setup(), *_say(*SPOKEN), *_keys("48"), None, *_keys("21#")])
    assert entries == ["4821"] and _by_value(r)["account_last4"] == ("4821", "keypad")
    events = _spy_on_events(monkeypatch)
    _, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), {"pause": 0.4 * Q}, None, {"pause": 3 * LONG}])
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[0], Q)


def test_messages_before_setup_are_ignored(make_ctx, clock):  # fix 5
    # Speaker events are subscribed now, so one can come before the setup. Only a setup that does not
    # verify rejects the socket (test_bad_token_or_wrong_number_fails_closed).
    early = [_info("agentSpeaking", "on"), _info("clientSpeaking", "on"), {"type": "mystery"}, None, ["x"]]
    ws = FakeWS([*early, _good_setup(), *_say(*SPOKEN), *_keys("4821#")])
    r = asyncio.run(run_relay(make_ctx(), ScriptedAgent(), ws, verify=_verify, clock=clock(), keypad_timeout=FAST))
    assert ws.closed is False and r.result == "answers_recorded"
    assert _by_value(r)["account_last4"] == ("4821", "keypad")


@pytest.mark.parametrize("key", ["#", "*"])
def test_quiet_clock_a_key_during_the_question_does_not_start_it_early(key, make_ctx, clock,
                                                                       monkeypatch):  # fix 7
    # A '#' or '*' at the company question means nothing there and says nothing. Pressed while the
    # question still plays, it does not start the clock at once: the pending start (here the
    # estimate) carries over.
    events = _spy_on_events(monkeypatch)
    ctx = make_ctx()
    lead = 0.6
    _, ws = _quiet_relay(ctx, clock, [_good_setup(), *_say("Yes, that's fine."), {"pause": 0.05}, *_keys(key),
                                      {"pause": 3 * LONG}], estimate=_estimate(lead=lead))
    company = [m["token"] for m in ws.sent].index(QUESTIONS[Phase.COMPANY].wording(ctx.script))
    silences = _silences(events)
    assert silences and _about(silences[0] - ws.sent_at[company], lead + Q)


def test_held_digits_follow_the_keypad_wait_not_the_quiet_clock(make_ctx, clock, monkeypatch):  # D7
    # The keypad wait is longer than the quiet wait here: with digits held, the entry still goes in
    # at the keypad wait, and no Silence comes first.
    events = _spy_on_events(monkeypatch)
    keypad_wait = 2 * Q4
    r, ws = _quiet_relay(make_ctx(), clock, [_good_setup(), *_say(*SPOKEN), *_keys("4821"), {"pause": 3 * LONG}],
                         keypad_timeout=keypad_wait)
    assert "Silence" not in [name for name, _ in events]
    keypad = [at for name, at in events if name == "Keypad"]
    assert len(keypad) == 1 and _about(keypad[0] - ws.delivered_at[-1], keypad_wait)
    assert r.result == "answers_recorded" and _by_value(r)["account_last4"] == ("4821", "keypad")


def test_keys_after_the_digits_check_in_are_recorded_over_the_relay(make_ctx, clock):  # C3
    # The check-in comes about 0.3 s after the digits question, and the keys 1 s after it, well
    # inside the long wait after the check-in.
    ctx = make_ctx()
    r, ws = _quiet_relay(ctx, clock, [_good_setup(), *_say(*SPOKEN), {"pause": 1.0}, *_keys("4821#")],
                         quiet_seconds_last4=0.3, after_check_in_seconds_last4=3.0)
    sent = [m["token"] for m in ws.sent]
    assert DIGITS_CHECK_IN in sent and sent[-1] == ctx.script.close()
    assert r.result == "answers_recorded" and _by_value(r)["account_last4"] == ("4821", "keypad")
