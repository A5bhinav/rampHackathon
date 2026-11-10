"""The runner (spec 0017, Task 3; spec 0048, A1).

`run_scenario` drives the real voice call (`run_call` with the agent under test and the scenario's
adversarial line) to a `CallResult`. `chain` then turns that call into the decision engine's
`CaseFacts` by production's own code, in production's order, and writes no copy of it:

1. the voice service's report: `report_body` and `with_connect_facts` (apps/voice app.py), which decide
   that answers ride along only on `answers_recorded` and that `dialed_number_is_pick` is true;
2. the core's arrival step (apps/core calls.py): the report is checked with `CallEndedRequest`, and the
   spoken digits are compared with `spoken_last_four` and `compare_last_four`. The rest of that step
   (hashing the digits and storing the event) affects no fact and is left out;
3. the facts: `call_facts` (apps/core call_facts.py), everything `Worker.decide` does after its reads.

Declared inputs: what production would read from its database or from Twilio, built by hand from the
scenario. Each one:
- the case: its legal name is the scenario's `vendor_name`; no trade names;
- the generation's events: this version's pick, which the call dialed; when the scenario says the
  dialed number was not eligible (`dialed_eligible_number: false`), a newer pick follows it; no
  earlier calls;
- the change-context result: `speaker_in_change_thread` and `offered_number_from_request` from the
  scenario; `speaker_known_before_request` unknown;
- Twilio's setup: the call's `direction` and its machine detection (the caller's `answered_by`);
- the case's accounts: `new_last4` and `old_last4`.

Facts production never computes this way (`declared.UNCOMPUTED`: `line_risk`, a false
`dialed_number_is_pick`) are applied where production would carry them, and only when the scenario
declares them (spec 0048, A3).
"""
from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import dialback_core.answers as core_answers
import dialback_core.call_facts as core_facts
import dialback_decision
import dialback_voice.app as voice_app
from dialback_core.calls import CallEndedRequest
from dialback_core.ports import ChangeFacts
from dialback_decision.facts import CaseFacts
from dialback_policy.script import Script
from dialback_policy.step import CallContext
from dialback_voice.agent import Agent
from dialback_voice.store import CallDial
from dialback_voice.transport import CallResult, Clock, run_call

from .scenario import Scenario
from .textline import AdversarialLine

# The callback the speech guard treats as valid, so the agent saying it in the disclosure is not a
# digit leak (apps/voice tests use the same constant).
CALLBACK = "(415) 555-0100"
CASE_ID = uuid.UUID(int=0x0048_0001)
PICK, NEWER_PICK = uuid.UUID(int=0x0048_0002), uuid.UUID(int=0x0048_0003)
PICK_E164 = "+14155550142"


def build_context(s: Scenario) -> CallContext:
    return CallContext(call_id=str(uuid.uuid5(uuid.NAMESPACE_URL, s.id)),
                       script=Script(s.customer_name, CALLBACK), vendor_names=(s.vendor_name,))


def run_scenario(s: Scenario, agent: Agent, *, clock: Clock) -> CallResult:
    return run_call(build_context(s), agent, AdversarialLine(s.caller), clock=clock)


@dataclasses.dataclass(frozen=True)
class Chained:
    report: dict[str, Any]       # the call_ended body as the core stores it (digits compared, not hashed)
    facts: CaseFacts


def chain(s: Scenario, r: CallResult) -> Chained:
    """The call's report and its facts, by production's code (see the module docstring)."""
    call_id = uuid.UUID(build_context(s).call_id)
    dial = CallDial(case_id=CASE_ID, attempt=1, number_e164=PICK_E164, customer_name=s.customer_name,
                    callback_number=CALLBACK, vendor_names=(s.vendor_name,), contact_point_id=PICK)
    body = voice_app.report_body(r, call_id, dial)
    setup = {"direction": "outbound-api" if s.facts.direction == "outbound" else "inbound",
             "customParameters": {"answered_by": s.caller.answered_by}}
    voice_app.with_connect_facts(body, setup, r)
    if s.facts.dialed_number_is_pick is False:                    # declared: production always says true
        body["dialed_number_is_pick"] = False
    CallEndedRequest.model_validate(body)                         # the core's own check of the report
    report = {k: v for k, v in body.items() if k not in ("case_id", "answers")}
    if "answers" in body:
        spoken = core_answers.spoken_last_four(body["answers"])
        report["last_four_result"] = dialback_decision.compare_last_four(spoken, s.facts.new_last4, s.facts.old_last4)
        report["answers"] = body["answers"]
    case = {"legal_name": s.vendor_name, "trade_names": []}
    generation = [{"type": "number_resolved", "body": {"contact_point_id": str(PICK)}}]
    if not s.facts.dialed_eligible_number:
        generation.append({"type": "number_resolved", "body": {"contact_point_id": str(NEWER_PICK)}})
    context = ChangeFacts(speaker_in_change_thread=s.facts.speaker_in_change_thread,
                          offered_number_from_request=s.facts.offered_number_from_request)
    facts = core_facts.call_facts(report, case, generation, context)
    if s.facts.line_risk is not None:                             # declared: nothing in production computes it
        facts = dataclasses.replace(facts, line_risk=s.facts.line_risk)
    return Chained(report, facts)


def facts_for(s: Scenario, r: CallResult) -> CaseFacts:
    return chain(s, r).facts
