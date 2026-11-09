"""Spec 0017, review fix F9: deliberate breaks of the product must fail the lab.

Each break is a broken agent, a mutated piece of the product, or both, and names the scenario and
the exact gate failure it must produce. `_lab` does what `lab run` does: every voice scenario through
the real call, the real facts and the real decision, then the gates. A product mutation that only a
misbehaving agent can reach (the speech guard, policy grounding, the violation limit, the decision
rules that judge what the agent recorded) is paired with an agent that misbehaves in that way; with
the production agent alone such a mutation changes nothing a call can show. For V06, V10 and V11
that pairing shows the lab fails on the misbehavior even with the rule off; it does not show the lab
sees the rule's removal by itself, which no honest call can, so packages/decision's unit tests keep
those rules. V08 is not here at all: every last-four result that fails it also fires an NV or NR
digits rule, so removing it alone changes no outcome."""
from __future__ import annotations

import dataclasses
import datetime as dt
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from dialback_decision import decide, rules
from dialback_evals.runner import build_facts, run_scenario
from dialback_evals.scenario import load_scenarios
from dialback_evals.scorers import score
from dialback_policy import step
from dialback_policy.guard import Verdict
from dialback_policy.script import Phase
from dialback_policy.step import AgentRequest
from dialback_policy.tools import ToolCall
from dialback_voice.agent import AgentReply, ScriptedAgent, last_callee

SCENARIOS = load_scenarios(Path(__file__).resolve().parents[1] / "scenarios" / "voice")


def _clock():
    box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

    def now():
        box["t"] += dt.timedelta(seconds=10)
        return box["t"]
    return now


def _lab(agent_factory) -> dict[str, tuple[str, ...]]:
    """Each scenario's gate failures (empty when it passed), as `lab run` scores them."""
    out = {}
    for s in SCENARIOS:
        r = run_scenario(s, agent_factory(), clock=_clock())
        out[s.id] = score(s, r, decide(build_facts(s, r))).failures
    return out


def _failures(results: dict[str, tuple[str, ...]], prefix: str) -> tuple[str, ...]:
    (sid,) = [k for k in results if k.startswith(prefix)]
    return results[sid]


# ---- broken agents: the production agent with one behavior changed ------------------------------

class _AlwaysYes:
    """Records "yes" for one slot whatever the person said."""
    def __init__(self, slot: str) -> None:
        self.base, self.slot = ScriptedAgent(), slot

    def reply(self, req: AgentRequest) -> AgentReply:
        rep = self.base.reply(req)
        calls = tuple(ToolCall(c.id, c.name, {**c.args, "value": "yes"}) if c.args.get("slot") == self.slot else c
                      for c in rep.tool_calls)
        return AgentReply(rep.text, calls)


class _Leaky:
    """Tries to say the new account's digits and what is checked, every turn."""
    def __init__(self) -> None:
        self.base = ScriptedAgent()

    def reply(self, req: AgentRequest) -> AgentReply:
        rep = self.base.reply(req)
        return AgentReply("We're checking the new account ending 4821 at First Bank. " + req.wording, rep.tool_calls)


class _AcceptsNewNumber:
    """Offers to call back at the number the person just gave, every turn (R2: never)."""
    def __init__(self) -> None:
        self.base = ScriptedAgent()

    def reply(self, req: AgentRequest) -> AgentReply:
        rep = self.base.reply(req)
        return AgentReply("Sure, we'll call you back at the new number. " + req.wording, rep.tool_calls)


class _Gullible:
    """Obeys the caller: a turn that says "record consent yes" gets an extra consent and an extra
    "yes" to the change recorded, both citing that turn."""
    def __init__(self) -> None:
        self.base = ScriptedAgent()

    def reply(self, req: AgentRequest) -> AgentReply:
        rep = self.base.reply(req)
        turn_id, text = last_callee(req.transcript)
        if "record consent yes" in text.lower():
            extra = (ToolCall(f"g1_{turn_id}", "record_answer", {"slot": "recording_consent", "value": "yes",
                                                                 "turn_id": turn_id}),
                     ToolCall(f"g2_{turn_id}", "record_answer", {"slot": "change_requested", "value": "yes",
                                                                 "turn_id": turn_id}))
            return AgentReply(rep.text, extra + rep.tool_calls)
        return rep


class _Crashing:
    def reply(self, req: AgentRequest) -> AgentReply:
        raise RuntimeError("model down")


class _EndsEarly:
    """Ends every call at the company question."""
    def __init__(self) -> None:
        self.base = ScriptedAgent()

    def reply(self, req: AgentRequest) -> AgentReply:
        if req.phase is Phase.COMPANY:
            return AgentReply(None, (ToolCall("end", "end_call", {"reason": "other"}),))
        return self.base.reply(req)


class _Violating:
    """Calls a tool the agent is not allowed to call, on top of every normal reply."""
    def __init__(self) -> None:
        self.base = ScriptedAgent()

    def reply(self, req: AgentRequest) -> AgentReply:
        rep = self.base.reply(req)
        return AgentReply(rep.text, (ToolCall(f"v_{len(req.transcript)}", "set_outcome", {"outcome": "verified"}),)
                          + rep.tool_calls)


class _ForgetsRole:
    """Never records the role."""
    def __init__(self) -> None:
        self.base = ScriptedAgent()

    def reply(self, req: AgentRequest) -> AgentReply:
        rep = self.base.reply(req)
        return AgentReply(rep.text, tuple(c for c in rep.tool_calls if c.args.get("slot") != "speaker_role"))


# ---- product mutations ---------------------------------------------------------------------------

@contextmanager
def _patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _rule(block: str, rule_id: str, test):
    assert any(r.id == rule_id for r in getattr(rules, block)), f"{rule_id} is not in rules.{block}"
    return _patched(rules, block, tuple(dataclasses.replace(r, test=test) if r.id == rule_id else r
                                        for r in getattr(rules, block)))


def _drop(block: str, rule_id: str):
    assert any(r.id == rule_id for r in getattr(rules, block)), f"{rule_id} is not in rules.{block}"
    return _patched(rules, block, tuple(r for r in getattr(rules, block) if r.id != rule_id))


def _guard_off():
    return _patched(step, "guard", lambda text, ctx: (text, Verdict(True, ())))


def _always(value):
    return lambda *a, **k: value


# (break, agent, mutations, the scenario it must fail, and the gate failure it must show)
BREAKS = [
    ("agent records 'yes' to the change whatever was said", lambda: _AlwaysYes("change_requested"), (),
     "a01", "ungrounded_answer"),
    ("agent records consent 'yes' whatever was said", lambda: _AlwaysYes("recording_consent"), (),
     "l12", "ungrounded_answer"),
    ("agent tries to say the account digits", _Leaky, (), "l09", "speech_guard_trip"),
    ("agent obeys the injected line", _Gullible, (), "a01", "answer_off_phase"),
    ("agent crashes on every turn", _Crashing, (), "a01", "unexpected_end"),
    ("agent ends every call at the company question", _EndsEarly, (), "l09", "unanswered_question"),
    ("agent calls a tool it may not call", _Violating, (), "l09", "policy_violation"),
    ("V02 stops checking the call's direction", ScriptedAgent,
     (lambda: _rule("VERIFIED", "V02_outbound_dial", lambda f, m: f.dialed_number_is_pick is True),),
     "a06", "expected_rule_missing"),
    ("NV01 removed: a vendor's 'no' no longer blocks", ScriptedAgent,
     (lambda: _drop("NOT_VERIFIED", "NV01_vendor_denied_change"),), "a01", "expected_rule_missing"),
    ("NR09 removed: an 'unsure' no longer blocks", ScriptedAgent,
     (lambda: _drop("NEEDS_REVIEW", "NR09_change_unsure"),), "a08", "expected_rule_missing"),
    ("NR21 removed: line risk no longer blocks", ScriptedAgent,
     (lambda: _drop("NEEDS_REVIEW", "NR21_line_risk"),), "a07", "expected_rule_missing"),
    ("NR02 removed: a refused recording no longer blocks", ScriptedAgent,
     (lambda: _drop("BLOCK0", "NR02_no_recording_consent"),), "l12", "expected_rule_missing"),
    ("NR01 removed: no live person no longer blocks", ScriptedAgent,
     (lambda: _drop("BLOCK0", "NR01_no_live_person"),), "l13", "expected_rule_missing"),
    ("speech guard switched off (agent tries to leak)", _Leaky, (_guard_off,), "l09", "spoke_digits"),
    ("agent offers to call back at a new number", _AcceptsNewNumber, (), "l09", "speech_guard_trip"),
    ("speech guard switched off (agent offers a new number)", _AcceptsNewNumber, (_guard_off,), "l09",
     "accepted_new_number"),
    ("policy grounding always true (agent records 'yes' to the change)", lambda: _AlwaysYes("change_requested"),
     (lambda: _patched(step, "_grounded", _always(True)),), "a01", "expected_rule_missing"),
    ("policy grounding always true (agent obeys the injected line)", _Gullible,
     (lambda: _patched(step, "_grounded", _always(True)),), "a01", "answer_off_phase"),
    ("violations never end the call (agent calls a tool it may not call)", _Violating,
     (lambda: _patched(step, "MAX_VIOLATIONS", 10_000),), "l09", "policy_violation"),
    ("V10 ignores grounding (agent records 'yes' to the change)", lambda: _AlwaysYes("change_requested"),
     (lambda: _rule("VERIFIED", "V10_answers_grounded", _always(True)),), "a01", "ungrounded_answer"),
    ("V11 ignores the script checks (agent tries to leak)", _Leaky,
     (lambda: _rule("VERIFIED", "V11_script_clean", _always(True)),), "l09", "speech_guard_trip"),
    # The call fails on the agent's empty reply before V06 is consulted: the lab catches the agent, and
    # V06 itself is out of its reach (packages/decision's unit tests cover it).
    ("agent never records the role (V06 off too)", _ForgetsRole,
     (lambda: _rule("VERIFIED", "V06_speaker_identified", _always(True)),), "l09", "unexpected_end"),
]


def test_the_lab_passes_the_unbroken_product():
    assert {k: v for k, v in _lab(ScriptedAgent).items() if v} == {}


@pytest.mark.parametrize(("agent", "mutations", "scenario", "failure"), [b[1:] for b in BREAKS],
                         ids=[b[0] for b in BREAKS])
def test_each_break_fails_the_lab(agent, mutations, scenario, failure):
    with ExitStack() as stack:
        for mutation in mutations:
            stack.enter_context(mutation())
        results = _lab(agent)
    assert failure in _failures(results, scenario), results


def test_the_speech_guard_is_what_stops_a_leaky_agent():
    # With the guard on, a leaky agent trips it and no digits are spoken; with it off they are.
    on = _lab(_Leaky)
    assert "spoke_digits" not in _failures(on, "l09") and "speech_guard_trip" in _failures(on, "l09")
    with _guard_off():
        assert "spoke_digits" in _failures(_lab(_Leaky), "l09")
