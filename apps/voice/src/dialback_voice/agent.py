"""The Agent port: the model sits behind this, so it never touches a hold, a number or an outcome.

`step` gives the model a question and the quoted transcript (R9) and gets back text to say and
tool calls, which `step` validates, grounds and guards before anything is spoken or recorded.
Live calls and the lab use Claude; tests and the dev stack use `ScriptedAgent`, a labeled double."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from dialback_policy.guard import digit_runs
from dialback_policy.script import PHASE_SLOT, Phase
from dialback_policy.step import AgentRequest
from dialback_policy.tools import ToolCall

# Word-boundary matches that agree with step's own grounding (step keeps the final say).
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|correct|absolutely|definitely|certainly|of course"
                  r"|go ahead|fine|okay|ok)\b")
_NO = re.compile(r"\b(no|nope|nah|not|didn't|didnt|did not|don't|dont|never|won't|wont)\b")
_UNSURE = re.compile(r"\b(not sure|unsure|don't know|dont know|no idea|not certain|maybe)\b")
_REFUSE = re.compile(r"\b(rather not|refuse|won't say|wont say|not comfortable)\b")


@dataclass(frozen=True)
class AgentReply:
    """What the model returns for one turn. `step` validates both before anything happens."""
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class Agent(Protocol):
    def reply(self, request: AgentRequest) -> AgentReply: ...


def last_callee(transcript: tuple[tuple[int, str, str], ...]) -> tuple[int, str]:
    """The id and text of the most recent callee turn, which an answer must cite."""
    for turn_id, speaker, text in reversed(transcript):
        if speaker == "callee":
            return turn_id, text
    raise ValueError("no callee turn to answer")


class ScriptedAgent:
    """A deterministic stand-in for the model. It reads only the transcript it is given, records the
    callee's answer into the phase's slot, and leaves question wording to code (text=None), so it
    never trips the speech guard. It is the far side of the contract the live model must meet."""

    def reply(self, request: AgentRequest) -> AgentReply:
        turn_id, text = last_callee(request.transcript)
        low = text.lower()
        phase = request.phase
        slot = PHASE_SLOT[phase]
        if phase is Phase.CONSENT:
            value = "yes" if (_YES.search(low) and not _NO.search(low)) else "no"
        elif phase is Phase.CHANGE:
            if _UNSURE.search(low):
                value = "unsure"
            elif _YES.search(low) and not _NO.search(low):
                value = "yes"
            else:
                value = "no"
        elif phase is Phase.LAST4:
            runs = digit_runs(text)
            value = runs[0] if runs else ("refused" if _REFUSE.search(low) else "dont_know")
        else:
            value = text.strip()
        call = ToolCall(id=f"tc_{turn_id}", name="record_answer",
                        args={"slot": slot, "value": value, "turn_id": turn_id})
        return AgentReply(tool_calls=(call,))
