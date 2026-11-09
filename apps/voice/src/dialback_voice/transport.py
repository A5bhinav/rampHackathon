"""The text phone line: drive policy.step() to the end with an Agent and a Line, no Twilio.

run_call feeds step() the line's events, speaks what step says, asks the Agent when step asks,
and reports the End. It is the same loop the Twilio ConversationRelay adapter (next spec) runs
around the same step(); the Line and Agent are the only things that change between text and live.
Any agent error, timeout or empty reply becomes AgentFailed, so the call fails closed (R7)."""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dialback_policy import step as policy
from dialback_policy.script import Phase

from .agent import Agent

Clock = Callable[[], dt.datetime]


def wall_clock() -> dt.datetime:
    """The live clock: an aware UTC datetime, which the policy stamps on every answer (spec 0034, B2)."""
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True)
class CallResult:
    end: policy.End
    answers: dict[str, Any]          # call-answers.schema.json CallAnswersInTransit
    checks: dict[str, Any]           # CallChecks, computed by code
    transcript: tuple[Any, ...]

    @property
    def result(self) -> str:
        return self.end.result


class Line(Protocol):
    """The far end: a scripted vendor now, the Twilio ConversationRelay WebSocket later."""
    def connect(self, at: dt.datetime) -> policy.Event: ...
    def hear(self, lines: tuple[str, ...]) -> None: ...
    def next(self, at: dt.datetime, phase: Phase | None) -> policy.Event: ...


def run_call(ctx: policy.CallContext, agent: Agent, line: Line, *, clock: Clock,
             max_duration: dt.timedelta | None = None, max_steps: int = 500) -> CallResult:
    start = clock()
    state = policy.start(ctx)
    event: policy.Event = line.connect(start)
    for _ in range(max_steps):
        out = policy.step(state, event)
        state = out.state
        if out.say:
            line.hear(out.say)
        if out.end is not None:
            return CallResult(end=out.end, answers=policy.answers_object(state),
                              checks=policy.checks(state), transcript=state.turns)
        if out.ask_agent is not None:
            event = _ask(agent, out.ask_agent, clock())
            continue
        at = clock()
        if max_duration is not None and at - start >= max_duration:
            event = policy.TimedOut(at)                 # the call ran past its limit (spec 0012, D44)
        else:
            event = line.next(at, state.phase)
    raise RuntimeError("the call did not reach an end within max_steps")  # step caps turns; a guard


def _ask(agent: Agent, request: policy.AgentRequest, at: dt.datetime) -> policy.Event:
    """Call the model behind the Agent port. Any error, timeout or empty reply fails closed (R7)."""
    try:
        reply = agent.reply(request)
    except TimeoutError as exc:
        return policy.AgentFailed(at, f"timeout:{exc}")
    except Exception as exc:  # noqa: BLE001  a model bug must never leave the call running
        return policy.AgentFailed(at, type(exc).__name__)
    if reply is None or (not (reply.text or "").strip() and not reply.tool_calls):
        return policy.AgentFailed(at, "empty_reply")   # blank text with no tool call says nothing
    return policy.AgentReplied(at, reply.text, tuple(reply.tool_calls))


@dataclass(frozen=True)
class VendorScript:
    """One scripted vendor for the text line and the tests: phase by phase, what the callee does."""
    answered_by: str = "human"          # human | machine
    consent: str = "yes"                # yes | no
    company: str = "Acme Fasteners"
    department: str = "Accounts payable"
    name: str = "Jordan Lee"
    role: str = "AP clerk"
    change: str = "yes"                 # yes | no | unsure
    last4: str = "4821"
    last4_mode: str = "keypad"          # keypad | speech | refuse | dont_know
    hang_up_at: Phase | None = None     # hang up when asked this phase, before answering it
    silent: bool = False                # never speaks: every turn is silence


_CHANGE_LINE = {"yes": "Yes, we did.", "no": "No, we didn't.", "unsure": "I'm not sure."}


class ScriptedVendor:
    """A deterministic far end. It answers by the phase it is asked, not by parsing the agent's
    words, so a call runs the same way every time."""

    def __init__(self, script: VendorScript) -> None:
        self.script = script
        self.heard: tuple[str, ...] = ()

    def connect(self, at: dt.datetime) -> policy.Event:
        return policy.Connected(at, "machine" if self.script.answered_by == "machine" else "human")

    def hear(self, lines: tuple[str, ...]) -> None:
        self.heard = tuple(lines)

    def next(self, at: dt.datetime, phase: Phase | None) -> policy.Event:
        s = self.script
        if s.silent:
            return policy.Silence(at)
        if s.hang_up_at is not None and phase == s.hang_up_at:
            return policy.Hangup(at)
        if phase is Phase.CONSENT:
            return policy.CalleeSaid(at, "Yes, that's fine." if s.consent == "yes" else "No, I'd rather not.")
        if phase is Phase.COMPANY:
            return policy.CalleeSaid(at, s.company)
        if phase is Phase.DEPARTMENT:
            return policy.CalleeSaid(at, s.department)
        if phase is Phase.NAME:
            return policy.CalleeSaid(at, s.name)
        if phase is Phase.ROLE:
            return policy.CalleeSaid(at, s.role)
        if phase is Phase.CHANGE:
            return policy.CalleeSaid(at, _CHANGE_LINE[s.change])
        if phase is Phase.LAST4:
            if s.last4_mode == "keypad":
                return policy.Keypad(at, s.last4)
            if s.last4_mode == "speech":
                return policy.CalleeSaid(at, "They're " + " ".join(s.last4) + ".")
            if s.last4_mode == "refuse":
                return policy.CalleeSaid(at, "I'd rather not say.")
            return policy.CalleeSaid(at, "I don't have that handy.")
        return policy.CalleeSaid(at, "Okay.")
