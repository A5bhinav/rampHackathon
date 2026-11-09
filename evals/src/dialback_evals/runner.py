"""The runner (spec 0017, Task 3).

`run_scenario` drives the real voice call (`run_call` with the agent under test and the scenario's
adversarial line) to a `CallResult`. `build_facts` turns that `CallResult` into the decision
engine's `CaseFacts` the way production does (apps/core worker): the recorded answers, the call's
checks, `dialed_out` from the call's direction, who answered, `company_matches` by production's own
`every_name_matches`, and `last_four_result` by production's `compare_last_four` on what was said. Only what the Trust Ledger would know is declared by the scenario (Q57)."""
from __future__ import annotations

import uuid

from dialback_core.answers import spoken_last_four
from dialback_core.company import every_name_matches
from dialback_decision import compare_last_four
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
        # production: the core compares what was said with the case's accounts (apps/core calls.py)
        last_four_result=compare_last_four(spoken_last_four(r.answers), s.facts.new_last4, s.facts.old_last4),
        dialed_number_is_pick=s.facts.dialed_number_is_pick,
        speaker_in_change_thread=s.facts.speaker_in_change_thread,
        offered_number_from_request=s.facts.offered_number_from_request,
        line_risk=s.facts.line_risk,
        dialed_out=s.facts.direction == "outbound",          # production: the call's direction
        answered_by=s.caller.answered_by,                  # who picked up, as the line reported it
        company_matches=every_name_matches(values(answers, "company_named"), s.vendor_name),
    )
