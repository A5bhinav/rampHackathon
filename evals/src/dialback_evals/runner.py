"""The runner (spec 0017, Task 3).

`run_scenario` drives the real voice call (`run_call` with the agent under test and the scenario's
adversarial line) to a `CallResult`. `build_facts` turns that `CallResult` into the decision
engine's `CaseFacts`: the recorded answers plus the scenario's declared facts, and the three facts
the Ruling supplies here (the lab always dials outbound, the caller sets who answered, and
`company_matches` is computed from the recorded company against the scenario's vendor)."""
from __future__ import annotations

import uuid

from dialback_decision.facts import CallChecks, CaseFacts, facts_from_answers, values
from dialback_policy.script import Script
from dialback_policy.step import CallContext
from dialback_voice.agent import Agent
from dialback_voice.transport import CallResult, Clock, run_call

from .scenario import Scenario
from .textline import AdversarialLine

# The callback the speech guard treats as valid, so the agent saying it in the disclosure is not a
# digit leak (apps/voice tests use the same constant).
_CALLBACK = "(415) 555-0100"


def build_context(s: Scenario) -> CallContext:
    return CallContext(call_id=str(uuid.uuid5(uuid.NAMESPACE_URL, s.id)),
                       script=Script(s.customer_name, _CALLBACK), vendor_names=(s.vendor_name,))


def run_scenario(s: Scenario, agent: Agent, *, clock: Clock) -> CallResult:
    return run_call(build_context(s), agent, AdversarialLine(s.caller), clock=clock)


def build_facts(s: Scenario, r: CallResult) -> CaseFacts:
    answers = r.answers["answers"]
    return facts_from_answers(
        answers,
        call_result=r.end.result,
        checks=CallChecks(**r.checks),
        dialed_eligible_number=s.facts.dialed_eligible_number,
        last_four_result=s.facts.last_four_result,
        dialed_number_is_pick=s.facts.dialed_number_is_pick,
        speaker_in_change_thread=s.facts.speaker_in_change_thread,
        offered_number_from_request=s.facts.offered_number_from_request,
        dialed_out=True,                                    # the lab always dials outbound (Ruling T1/T3)
        answered_by=s.caller.answered_by,                  # the caller set who picked up
        company_matches=_company_matches(answers, s.vendor_name),
    )


def _company_matches(answers: list[dict], vendor_name: str) -> bool | None:
    """Every recorded company name against the real vendor. None when none was recorded (R7)."""
    said = values(answers, "company_named")
    if not said:
        return None
    target = _norm(vendor_name)
    return all(_norm(v) == target for v in said)


def _norm(v: str) -> str:
    """Casefold, drop punctuation and collapse spacing, like dialback_decision.facts._name."""
    return " ".join("".join(c for c in v.casefold() if c.isalnum() or c.isspace()).split())
