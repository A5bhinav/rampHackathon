"""The lab's agent under test (spec 0017, Task 3; Ruling).

`LabAgent` behaves like the voice service's `ScriptedAgent`, but it also records `speaker_role` from
the name-and-role turn, so a clean call can satisfy the V06 verify condition and reach a `verified`
decision. The production `ScriptedAgent` records only `speaker_name` for the NAME_ROLE phase (and the
live agent slated for #40 does the same), so a call it drives never records a role and never verifies:
a gap this lab surfaced (see the ledger). Once the voice agents record the role, the live-model gate
runs against the real agent. Deterministic, so the gate stays reproducible."""
from __future__ import annotations

from dialback_policy.script import Phase
from dialback_policy.step import AgentRequest
from dialback_policy.tools import ToolCall
from dialback_voice.agent import AgentReply, ScriptedAgent, last_callee


class LabAgent:
    def __init__(self) -> None:
        self._base = ScriptedAgent()

    def reply(self, request: AgentRequest) -> AgentReply:
        base = self._base.reply(request)
        if request.phase is not Phase.NAME_ROLE:
            return base
        try:
            turn_id, text = last_callee(request.transcript)
        except ValueError:
            return base
        role = text.split(",", 1)[1].strip() if "," in text else ""
        if not role:
            return base                                   # no role said: leave it, V06 will fail closed
        role_call = ToolCall(id=f"tc_{turn_id}_role", name="record_answer",
                             args={"slot": "speaker_role", "value": role, "turn_id": turn_id})
        return AgentReply(text=base.text, tool_calls=base.tool_calls + (role_call,))
