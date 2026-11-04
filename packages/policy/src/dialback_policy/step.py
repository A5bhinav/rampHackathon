"""policy.step(state, event) -> Step: the only dialogue logic in Dialback (ADR-5; spec 0012).

Pure: no I/O, no clock, no model. A transport (the text phone line, or the Twilio adapter) feeds
it events and does what each Step says: speak `say`, call the agent when `ask_agent` is set and
feed back `AgentReplied`, and report `end` to the core with `answers_object()` and `checks()`.

Code owns the order of questions, what gets recorded, and every way a call ends. The model only
words the questions and proposes tool calls, which are validated (tools.py), grounded in the
transcript, and guarded (guard.py) before anything is spoken. Anything unexpected ends the call
`failed`, and the hold stays (R7).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .guard import GuardContext, check, digit_runs, guard, words
from .script import FALLBACK_LINE, ORDER, PHASE_SLOT, QUESTIONS, REASK, STILL_THERE, Phase, Script
from .tools import NO_DIGITS, ToolCall, ToolError, validate

MAX_CALLEE_TURNS = 30
MAX_SILENCES = 3
MAX_VIOLATIONS = 2
REDIAL_AFTER = dt.timedelta(minutes=5)
CALLBACK_HOUR = 10
CALLBACK_TIME_QUESTION = "What time would work best for us to call you back?"
AGENT_TOOLS = ("record_answer", "schedule_callback", "end_call")

_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|correct|right|okay|ok|fine|absolutely|definitely|certainly|of course"
                  r"|go ahead|we did|i did|uh huh|affirmative)\b")
_NO = re.compile(r"\b(no|nope|nah|not|didn't|did not|never|don't|haven't|negative)\b")
_UNSURE = re.compile(r"\b(not sure|unsure|don't know|dont know|no idea|maybe|not certain|i think)\b")
_NOT_A_NO = re.compile(r"\b(no problem|not a problem|no worries)\b")
# Code's floor for the pressure flag (spec 0012, D6). The model may add the flag too.
PRESSURE = re.compile(
    r"\b(just approve|approve it|approve this|release (it|the payment|the funds)|wire it|asap|urgent(ly)?|right away"
    r"|immediately|(send|pay|release) (it|this|us|the payment) (today|now)|needs? (it|this|the payment|payment)"
    r"( paid)? (today|now)|my (boss|ceo|cfo|manager|owner) (needs|wants|said|is waiting)|late fee)\b")
_NEW_NUMBER = re.compile(r"\b(i'll|i will|we'll|we will) (call|reach|dial) (you )?(at|on) (that|this|the other|the new"
                         r"|your cell|your mobile|another)\b")


@dataclass(frozen=True)
class CallContext:
    """What code knows about the call. The model sees only the names and the callback number."""
    call_id: str
    script: Script
    vendor_names: tuple[str, ...]
    vendor_tz: str = "America/Los_Angeles"


@dataclass(frozen=True)
class Turn:
    id: int
    speaker: Literal["agent", "callee"]
    text: str
    asked: Phase | None = None  # agent turns: the question this turn asked


@dataclass(frozen=True)
class End:
    result: str                 # CallEndedBody.result
    reason: str                 # why, for the call log
    retry_at: dt.datetime | None = None


@dataclass(frozen=True)
class State:
    ctx: CallContext
    turns: tuple[Turn, ...] = ()
    answers: tuple[dict[str, Any], ...] = ()
    answered_by: str | None = None
    violations: int = 0
    guard_trips: int = 0
    silences: int = 0
    ungrounded: bool = False
    callback_asked: bool = False
    pressure_flagged: bool = False
    ended: End | None = None

    @property
    def consent(self) -> str | None:
        return next((a["value"] for a in self.answers if a["slot"] == "recording_consent"), None)

    @property
    def phase(self) -> Phase | None:
        """The first question not yet answered. A new speaker means the name is asked again."""
        changed = max((a["turn_id"] for a in self.answers if a["slot"] == "flag" and a["value"] == "speaker_changed"),
                      default=-1)
        for phase in ORDER:
            slot = PHASE_SLOT[phase]
            if not any(a["slot"] == slot and (phase is not Phase.NAME_ROLE or a["turn_id"] >= changed)
                       for a in self.answers):
                return phase
        return None


@dataclass(frozen=True)
class AgentRequest:
    """Everything the model gets. The callee's words are quoted data, never instructions (R9)."""
    phase: Phase
    goal: str
    wording: str
    next_goal: str | None
    customer_name: str
    vendor_name: str
    callback_number: str
    transcript: tuple[tuple[int, str, str], ...]
    tools: tuple[str, ...] = AGENT_TOOLS


@dataclass(frozen=True)
class Step:
    state: State
    say: tuple[str, ...] = ()
    ask_agent: AgentRequest | None = None
    end: End | None = None


# Events. Every event carries the time it happened; step never reads a clock.
@dataclass(frozen=True)
class Connected:
    at: dt.datetime
    answered_by: Literal["human", "machine"]


@dataclass(frozen=True)
class CalleeSaid:
    at: dt.datetime
    text: str


@dataclass(frozen=True)
class Keypad:
    at: dt.datetime
    digits: str


@dataclass(frozen=True)
class AgentReplied:
    at: dt.datetime
    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class AgentFailed:
    at: dt.datetime
    reason: str


@dataclass(frozen=True)
class Silence:
    at: dt.datetime


@dataclass(frozen=True)
class Hangup:
    at: dt.datetime


@dataclass(frozen=True)
class TimedOut:
    at: dt.datetime


Event = Connected | CalleeSaid | Keypad | AgentReplied | AgentFailed | Silence | Hangup | TimedOut


def start(ctx: CallContext) -> State:
    return State(ctx=ctx)


def step(state: State, event: Event) -> Step:
    if state.ended is not None:
        return Step(state)
    match event:
        case Connected():
            return _connected(state, event)
        case CalleeSaid():
            return _callee_said(state, event)
        case Keypad():
            return _keypad(state, event)
        case AgentReplied():
            return _agent_replied(state, event)
        case AgentFailed():
            return _end(state, "failed", f"agent_error:{event.reason}", state.ctx.script.goodbye())
        case Silence():
            state = replace(state, silences=state.silences + 1)
            if state.silences >= MAX_SILENCES:
                return _end(state, "failed", "silence", state.ctx.script.goodbye())
            return _speak(state, STILL_THERE, None)
        case Hangup():
            if state.consent is None:
                return _end(state, "declined", "hung_up_before_consent", None)
            return _end(state, "failed", "dropped", None, retry_at=event.at + REDIAL_AFTER)
        case TimedOut():
            return _end(state, "failed", "call_too_long", state.ctx.script.goodbye())
    raise TypeError(f"unknown event {event!r}")


def answers_object(state: State) -> dict[str, Any]:
    """The in-transit call answers (call-answers.schema.json, CallAnswersInTransit)."""
    return {"schema_version": "call-answers/v0", "call_id": state.ctx.call_id,
            "answers": [dict(a) for a in state.answers]}


def checks(state: State) -> dict[str, Any]:
    """CallChecks, computed by code from the transcript, never by the model."""
    script = state.ctx.script
    agent_turns = [t for t in state.turns if t.speaker == "agent"]
    first = agent_turns[0].text if agent_turns else ""
    spoke_digits = False
    last_callee = ""
    for turn in state.turns:
        if turn.speaker == "callee":
            last_callee = turn.text
        elif "digits" in check(turn.text, GuardContext(callee_last_turn=last_callee,
                                                       callback_number=script.callback_number)).reasons:
            spoke_digits = True
    return {
        "disclosure_first": first in (script.disclosure(), script.voicemail()),
        "answers_grounded": not state.ungrounded,
        "agent_spoke_account_digits": spoke_digits,
        "new_number_accepted": any(_NEW_NUMBER.search(t.text.lower()) for t in agent_turns),
        "policy_violations": state.violations,
        "speech_guard_trips": state.guard_trips,
    }


# --- the handlers ------------------------------------------------------------------------------

def _connected(state: State, event: Connected) -> Step:
    script = state.ctx.script
    state = replace(state, answered_by=event.answered_by)
    if event.answered_by == "machine":
        return _end(state, "voicemail", "voicemail", script.voicemail())
    state = _add(state, "agent", script.disclosure())
    state = _add(state, "agent", script.consent_question(), asked=Phase.CONSENT)
    return Step(state, say=(script.disclosure(), script.consent_question()))


def _callee_said(state: State, event: CalleeSaid) -> Step:
    state = replace(_add(state, "callee", event.text), silences=0)
    turn = state.turns[-1].id
    if sum(t.speaker == "callee" for t in state.turns) > MAX_CALLEE_TURNS:
        return _end(state, "failed", "turn_cap", state.ctx.script.goodbye())
    if state.consent == "yes" and not state.pressure_flagged and PRESSURE.search(event.text.lower()):
        state = replace(_record(state, "flag", "pressure_to_approve", turn, f"code_{turn}", event.at, "speech"),
                        pressure_flagged=True)
    return Step(state, ask_agent=_request(state))


def _keypad(state: State, event: Keypad) -> Step:
    if state.phase is not Phase.LAST4 or state.consent != "yes":
        return Step(state)  # keys pressed outside the digits question mean nothing
    state = _add(state, "callee", "[keypad]")  # the digits never enter the transcript
    turn = state.turns[-1].id
    if not re.fullmatch(r"[0-9]{1,4}", event.digits):
        return _speak(state, f"{REASK} {QUESTIONS[Phase.LAST4].wording(state.ctx.script)}", Phase.LAST4)
    state = _record(state, "account_last4", event.digits, turn, f"dtmf_{turn}", event.at, "keypad")
    return _advance(state, None)


def _agent_replied(state: State, event: AgentReplied) -> Step:
    script = state.ctx.script
    violated = False
    for call in event.tool_calls:
        callee_turns = frozenset(t.id for t in state.turns if t.speaker == "callee")
        try:
            args = validate(call, callee_turns=callee_turns)
            if call.name == "record_answer" and args["slot"] != "recording_consent" and state.consent != "yes":
                raise ToolError("before_consent", "nothing is recorded before consent")
        except ToolError:
            violated = True
            state = replace(state, violations=state.violations + 1)
            if state.violations >= MAX_VIOLATIONS:
                return _end(state, "failed", "policy_violations", script.goodbye())
            continue

        if call.name == "record_answer":
            slot, value, turn = args["slot"], args["value"], args["turn_id"]
            if not _grounded(state, slot, value, turn):
                state = replace(state, ungrounded=True)
            state = _record(state, slot, value, turn, call.id, event.at, "speech")
            if slot == "recording_consent" and value == "no":
                return _end(state, "answers_recorded", "consent_refused", script.consent_refused())
            if slot == "flag" and value == "opt_out":
                return _end(state, "answers_recorded", "opt_out", script.goodbye())

        elif call.name == "schedule_callback":
            window = args["window"]
            if window is None and not state.callback_asked:
                return _speak(replace(state, callback_asked=True), CALLBACK_TIME_QUESTION, state.phase)
            retry = window["start"].astimezone(dt.UTC) if window else None
            if retry is None or retry <= event.at:
                retry = _next_business_morning(event.at, state.ctx.vendor_tz)
            return _end(state, "callback_requested", "callback", script.callback_close(), retry_at=retry)

        elif call.name == "end_call":
            result = "answers_recorded" if state.consent == "yes" else "declined"
            line = script.close() if state.phase is None else script.goodbye()
            return _end(state, result, f"end_call:{args['reason']}", line)

    if violated:
        phase = state.phase or Phase.CONSENT
        return _speak(state, f"{REASK} {QUESTIONS[phase].wording(script)}", phase)
    return _advance(state, event.text)


def _advance(state: State, text: str | None) -> Step:
    """Ask the next question: in the model's words if they pass the guard, else the fixed wording."""
    phase = state.phase
    script = state.ctx.script
    if phase is None:
        return _end(state, "answers_recorded", "completed", script.close())
    wording = QUESTIONS[phase].wording(script)
    if not text or not text.strip():
        return _speak(state, wording, phase)
    said, verdict = guard(text, _guard_context(state))
    if not verdict.ok:
        return _speak(replace(state, guard_trips=state.guard_trips + 1), f"{FALLBACK_LINE} {wording}", phase)
    return _speak(state, said, phase)


# --- helpers -----------------------------------------------------------------------------------

def _add(state: State, speaker: Literal["agent", "callee"], text: str, asked: Phase | None = None) -> State:
    return replace(state, turns=state.turns + (Turn(len(state.turns), speaker, text, asked),))


def _speak(state: State, line: str, asked: Phase | None) -> Step:
    return Step(_add(state, "agent", line, asked), say=(line,))


def _end(state: State, result: str, reason: str, line: str | None, retry_at: dt.datetime | None = None) -> Step:
    if line:
        state = _add(state, "agent", line)
    end = End(result=result, reason=reason, retry_at=retry_at)
    return Step(replace(state, ended=end), say=(line,) if line else (), end=end)


def _record(state: State, slot: str, value: str, turn: int, tool_call_id: str, at: dt.datetime,
            method: str) -> State:
    answer = {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": tool_call_id,
              "captured_at": at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "capture_method": method}
    return replace(state, answers=state.answers + (answer,))


def _guard_context(state: State) -> GuardContext:
    callee = [t.text for t in state.turns if t.speaker == "callee"]
    named = [a["value"] for a in state.answers if a["slot"] == "company_named"]
    return GuardContext(vendor_names=state.ctx.vendor_names, named_company=named[-1] if named else "",
                        callee_last_turn=callee[-1] if callee else "",
                        callback_number=state.ctx.script.callback_number,
                        customer_name=state.ctx.script.customer_name)


def _request(state: State) -> AgentRequest:
    phase = state.phase or Phase.CONSENT
    script = state.ctx.script
    later = ORDER[ORDER.index(phase) + 1:] if phase in ORDER else ()
    return AgentRequest(
        phase=phase, goal=QUESTIONS[phase].goal, wording=QUESTIONS[phase].wording(script),
        next_goal=QUESTIONS[later[0]].goal if later else None, customer_name=script.customer_name,
        vendor_name=state.ctx.vendor_names[0] if state.ctx.vendor_names else "",
        callback_number=script.callback_number,
        transcript=tuple((t.id, t.speaker, t.text) for t in state.turns))


def _grounded(state: State, slot: str, value: str, turn_id: int) -> bool:
    """Does the cited callee turn really contain this answer? (spec 0012, D5)"""
    text = state.turns[turn_id].text
    if slot in ("recording_consent", "change_requested"):
        asked = next((t.asked for t in reversed(state.turns[:turn_id]) if t.speaker == "agent"), None)
        if asked is not (Phase.CONSENT if slot == "recording_consent" else Phase.CHANGE):
            return False  # one "yes" can't answer a question that wasn't the one just asked
        lowered = _NOT_A_NO.sub(" ", text.lower())
        cue = {"yes": _YES, "no": _NO, "unsure": _UNSURE}[value]
        return bool(cue.search(lowered))
    if slot in ("company_named", "department", "speaker_name", "speaker_role"):
        return words(value) in words(text)
    if slot == "account_last4":
        if value in NO_DIGITS:
            return True
        runs = digit_runs(text)
        return any(run == value or (len(value) == 4 and run.endswith(value)) for run in runs) or "".join(runs) == value
    if slot == "offered_number":
        digits = "".join(ch for ch in value if ch.isdigit())
        return digits in "".join(digit_runs(text)) if digits else words(value) in words(text)
    return True  # flags are judgment calls; code adds its own pressure flag


def _next_business_morning(at: dt.datetime, tz: str) -> dt.datetime:
    zone = ZoneInfo(tz)
    day = at.astimezone(zone).date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime(day.year, day.month, day.day, CALLBACK_HOUR, tzinfo=zone).astimezone(dt.UTC)
