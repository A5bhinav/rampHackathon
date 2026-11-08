"""The runner (spec 0017, Task 3): a scenario to a CallResult via the real run_call, and the
CallResult to CaseFacts, supplying the Ruling facts (dialed_out, answered_by, company_matches)."""
from __future__ import annotations

import datetime as dt

from dialback_decision import decide
from dialback_decision.facts import values
from dialback_evals.lab_agent import LabAgent
from dialback_evals.runner import build_facts, run_scenario
from dialback_evals.scenario import CallerBehavior, DeclaredFacts, Scenario
from dialback_voice.agent import ScriptedAgent


def _clock():
    box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

    def now():
        box["t"] += dt.timedelta(seconds=10)
        return box["t"]
    return now


def _legit() -> Scenario:
    return Scenario(id="legit-01", scenario_class="legit_bank_switch", is_attack=False,
                    customer_name="Northwind Traders", vendor_name="Acme Fasteners",
                    caller=CallerBehavior(consent="yes", company="Acme Fasteners", department="Accounts payable",
                                          name_role="Jordan Lee, AP clerk", change="yes",
                                          last4_mode="keypad", last4="4821"),
                    facts=DeclaredFacts(dialed_eligible_number=True, last_four_result="match",
                                        dialed_number_is_pick=True, speaker_in_change_thread=False,
                                        offered_number_from_request=False))


def test_runs_a_cooperative_scenario_to_answers_recorded():
    r = run_scenario(_legit(), ScriptedAgent(), clock=_clock())
    assert r.end.result == "answers_recorded"
    assert values(r.answers["answers"], "recording_consent") == ("yes",)


def test_build_facts_supplies_the_ruling_facts():
    s = _legit()
    facts = build_facts(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.last_four_result == "match"                            # declared
    assert facts.company_matches is True                               # computed from the recorded company vs vendor
    assert facts.dialed_out is True and facts.answered_by == "human"   # supplied (Ruling T1/T3)
    assert facts.checks.agent_spoke_account_digits is False            # read off CallResult.checks
    assert facts.recording_consent == ("yes",)


def test_lab_agent_records_role_so_a_clean_legit_call_verifies():
    # The verify path is real, not a tautology: with speaker_role recorded (LabAgent), a clean,
    # reachable legit scenario reaches "verified". ScriptedAgent alone omits the role (V06 unmet).
    s = _legit()
    facts = build_facts(s, run_scenario(s, LabAgent(), clock=_clock()))
    assert facts.speaker_role and facts.speaker_role[0].strip()
    assert decide(facts).outcome == "verified"
