"""Fail closed (R7, spec 0012 item 7): an agent error, a timeout, a malformed tool call, repeated
silence or an over-long call all end the call `failed`. None ever becomes a recorded "yes"."""
from __future__ import annotations

import datetime as dt

from dialback_policy.tools import ToolCall
from dialback_voice.agent import AgentReply
from dialback_voice.transport import ScriptedVendor, VendorScript, run_call


class RaisingAgent:
    def reply(self, request) -> AgentReply:
        raise RuntimeError("model exploded")


class TimingOutAgent:
    def reply(self, request) -> AgentReply:
        raise TimeoutError("model slow")


class BadToolAgent:
    """Always emits a tool call with a slot that does not exist, so step counts a policy violation."""
    def reply(self, request) -> AgentReply:
        turn = next(tid for tid, who, _ in reversed(request.transcript) if who == "callee")
        return AgentReply(tool_calls=(ToolCall(id="tc_bad", name="record_answer",
                                               args={"slot": "ssn", "value": "x", "turn_id": turn}),))


class SilentModelAgent:
    def reply(self, request) -> AgentReply:
        return AgentReply(text=None, tool_calls=())


class BlankReplyAgent:
    """Replies with blank text and no tool calls. Must fail closed at once as empty_reply, not loop
    turn after turn to the turn cap (review of A5bhinav/rampHackathon#36)."""
    def __init__(self) -> None:
        self.calls = 0

    def reply(self, request) -> AgentReply:
        self.calls += 1
        return AgentReply(text="   ", tool_calls=())


class FailsAfterConsent:
    """Records consent normally, then the model crashes on the next question, so a "yes" is on the
    books before the failure. Proves a recorded yes still fails closed (R7) and never releases."""
    def __init__(self) -> None:
        from dialback_voice.agent import ScriptedAgent
        self._inner = ScriptedAgent()
        self.calls = 0

    def reply(self, request) -> AgentReply:
        from dialback_policy.script import Phase
        self.calls += 1
        if request.phase is not Phase.CONSENT:
            raise RuntimeError("model crashed after consent")
        return self._inner.reply(request)


def _no_yes(result):
    return all(not (a["slot"] == "recording_consent" and a["value"] == "yes")
               for a in result.answers["answers"])


def test_agent_error_fails_closed(make_ctx, clock):
    r = run_call(make_ctx(), RaisingAgent(), ScriptedVendor(VendorScript()), clock=clock())
    assert r.end.result == "failed"
    assert _no_yes(r)


def test_agent_timeout_fails_closed(make_ctx, clock):
    r = run_call(make_ctx(), TimingOutAgent(), ScriptedVendor(VendorScript()), clock=clock())
    assert r.end.result == "failed"
    assert "timeout" in r.end.reason
    assert _no_yes(r)


def test_empty_agent_reply_fails_closed(make_ctx, clock):
    r = run_call(make_ctx(), SilentModelAgent(), ScriptedVendor(VendorScript()), clock=clock())
    assert r.end.result == "failed"
    assert _no_yes(r)


def test_two_bad_tool_calls_fail_closed(make_ctx, clock):
    r = run_call(make_ctx(), BadToolAgent(), ScriptedVendor(VendorScript()), clock=clock())
    assert r.end.result == "failed"
    assert r.end.reason == "policy_violations"
    assert r.answers["answers"] == []  # nothing was recorded


def test_repeated_silence_fails_closed(make_ctx, clock):
    from dialback_voice.agent import ScriptedAgent
    r = run_call(make_ctx(), ScriptedAgent(), ScriptedVendor(VendorScript(silent=True)), clock=clock())
    assert r.end.result == "failed"
    assert r.end.reason == "silence"


def test_call_too_long_fails_closed(make_ctx, clock):
    from dialback_voice.agent import ScriptedAgent
    r = run_call(make_ctx(), ScriptedAgent(), ScriptedVendor(VendorScript()),
                 clock=clock(), max_duration=dt.timedelta(seconds=5))
    assert r.end.result == "failed"
    assert r.end.reason == "call_too_long"


def test_a_blank_text_reply_fails_closed_at_once(make_ctx, clock):
    # A whitespace-only reply with no tool calls is as empty as text=None: it must fail closed
    # immediately as empty_reply, not loop turn after turn until the turn cap (slow and expensive
    # on a live call). The strong signal is that the agent is asked exactly once.
    agent = BlankReplyAgent()
    r = run_call(make_ctx(), agent, ScriptedVendor(VendorScript()), clock=clock())
    assert r.end.result == "failed"
    assert r.end.reason == "agent_error:empty_reply"
    assert agent.calls == 1
    assert _no_yes(r)


def test_a_crash_after_a_recorded_yes_still_fails_closed(make_ctx, clock):
    # The one fail-closed case that matters most: a consent "yes" is already recorded when the model
    # crashes. The crash must win (result=failed, so decide() holds the hold); the recorded yes must
    # never turn a failed call into a release. The other fail-closed tests all fail before consent.
    agent = FailsAfterConsent()
    r = run_call(make_ctx(), agent, ScriptedVendor(VendorScript(consent="yes")), clock=clock())
    assert r.end.result == "failed"
    assert agent.calls >= 2  # it failed after consent, not on it
    assert any(a["slot"] == "recording_consent" and a["value"] == "yes"
               for a in r.answers["answers"])  # and the yes really was on the books
