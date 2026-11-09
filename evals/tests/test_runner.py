"""The runner (spec 0017, Task 3): a scenario to a CallResult via the real run_call, and the
CallResult to CaseFacts, supplying the Ruling facts (dialed_out, answered_by, company_matches)."""
from __future__ import annotations

import dataclasses
import datetime as dt

from dialback_decision import decide
from dialback_decision.facts import values
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
                                          name="Jordan Lee", role="AP clerk", change="yes",
                                          last4_mode="keypad", last4="4821"),
                    facts=DeclaredFacts(dialed_eligible_number=True, last_four_result="match",
                                        dialed_number_is_pick=True, speaker_in_change_thread=False,
                                        offered_number_from_request=False))


def test_runs_a_cooperative_scenario_to_answers_recorded():
    r = run_scenario(_legit(), ScriptedAgent(), clock=_clock())
    assert r.end.result == "answers_recorded"
    assert values(r.answers["answers"], "recording_consent") == ("yes",)


def test_build_facts_reads_the_call_and_the_declared_facts():
    s = _legit()
    facts = build_facts(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.last_four_result == "match"                            # declared
    assert facts.company_matches is True                               # production's rule on the recorded name
    assert facts.dialed_out is True and facts.answered_by == "human"   # the scenario's direction; who picked up
    assert facts.checks.agent_spoke_account_digits is False            # read off CallResult.checks
    assert facts.recording_consent == ("yes",)


def test_the_production_agent_verifies_a_clean_legit_call():
    # The verify path is real, not a tautology, and runs the product's own agent: main's ScriptedAgent
    # records the role itself (spec 0034), so a clean, reachable legit scenario reaches "verified"
    # with no lab-side patch (review fix F2).
    s = _legit()
    facts = build_facts(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.speaker_role == ("AP clerk",)
    assert decide(facts).outcome == "verified"


def test_dialed_out_follows_the_scenario_direction():
    s = dataclasses.replace(_legit(), facts=dataclasses.replace(_legit().facts, direction="inbound"))
    facts = build_facts(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.dialed_out is False
    assert decide(facts).outcome != "verified"


def test_company_match_is_productions_rule():
    # Production compares word sets after dropping legal suffixes (dialback_core.company): saying the
    # whole name with a suffix matches, a part of it or a misspelling does not (review fix F2).
    def matched(said: str):
        s = dataclasses.replace(_legit(), caller=dataclasses.replace(_legit().caller, company=said))
        return build_facts(s, run_scenario(s, ScriptedAgent(), clock=_clock())).company_matches
    assert matched("Acme Fasteners LLC") is True
    assert matched("Acme") is False
    assert matched("Acme Fastners LLC") is False
