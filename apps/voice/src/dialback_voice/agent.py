"""The Agent port: the model sits behind this, so it never touches a hold, a number or an outcome.

`step` gives the model a question and the quoted transcript (R9) and gets back text to say and
tool calls, which `step` validates, grounds and guards before anything is spoken or recorded.
Live calls and the lab use Claude; tests and the dev stack use `ScriptedAgent`, a labeled double."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from dialback_policy.script import PHASE_SLOT, Phase
from dialback_policy.step import AgentRequest, read_yes_no
from dialback_policy.tools import ToolCall

# At the digits question (spoken digits never reach an agent; step re-asks for the keypad).
_REFUSE = re.compile(r"\b(rather not|refuse|won't say|wont say|not comfortable)\b")
_DONT_KNOW = re.compile(r"\b(don't know|dont know|no idea|not sure|don't have|dont have)\b")


@dataclass(frozen=True)
class AgentReply:
    """What the model returns for one turn. `step` validates text and tool calls before anything
    happens. `unclear` says the model could not tell what the callee answered: the transport turns
    it into AgentUnclear, nothing is recorded, and step asks once more (spec 0043, D6)."""
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    unclear: bool = False


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
    never trips the speech guard. It is the far side of the contract the live model must meet. It
    reads yes and no the policy's way (`read_yes_no`), so "Sure, no problem." is yes and "Hello?" is
    unclear (spec 0043, D9)."""

    def reply(self, request: AgentRequest) -> AgentReply:
        turn_id, text = last_callee(request.transcript)
        low = text.lower()
        phase = request.phase
        slot = PHASE_SLOT[phase]
        if phase is Phase.CONSENT or phase is Phase.CHANGE:
            value = read_yes_no(text, allow_unsure=phase is Phase.CHANGE)
        elif phase is Phase.LAST4:
            value = "refused" if _REFUSE.search(low) else "dont_know" if _DONT_KNOW.search(low) else "unclear"
        else:
            value = text.strip()
        if value == "unclear":
            return AgentReply(unclear=True)
        call = ToolCall(id=f"tc_{turn_id}", name="record_answer",
                        args={"slot": slot, "value": value, "turn_id": turn_id})
        return AgentReply(tool_calls=(call,))
