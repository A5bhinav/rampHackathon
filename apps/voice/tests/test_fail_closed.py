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
