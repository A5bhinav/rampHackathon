"""The runner (spec 0017, Task 3; spec 0048, A1): a scenario to a CallResult via the real run_call, and
the CallResult to CaseFacts through production's own chain (the voice service's report, the core's
digit comparison, call_facts), with only the declared inputs built by hand."""
from __future__ import annotations

import dataclasses
import datetime as dt

from dialback_decision import decide
from dialback_decision.facts import values
from dialback_evals.runner import facts_for, run_scenario
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
                    caller=CallerBehavior(company_is="vendor", consent="yes", company="Acme Fasteners", department="Accounts payable",
                                          name="Jordan Lee", role="AP clerk", change="yes",
                                          last4_mode="keypad", last4="4821"),
                    facts=DeclaredFacts(dialed_eligible_number=True, new_last4="4821",
                                        dialed_number_is_pick=True, speaker_in_change_thread=False,
                                        offered_number_from_request=False))


def test_runs_a_cooperative_scenario_to_answers_recorded():
    r = run_scenario(_legit(), ScriptedAgent(), clock=_clock())
    assert r.end.result == "answers_recorded"
    assert values(r.answers["answers"], "recording_consent") == ("yes",)


def test_facts_read_the_call_through_production():
    s = _legit()
    facts = facts_for(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.last_four_result == "match"                            # compared, as production does
    assert facts.company_matches is True                               # production's rule on the recorded name
    assert facts.dialed_out is True and facts.answered_by == "human"   # the scenario's direction; who picked up
    assert facts.checks.agent_spoke_account_digits is False            # read off CallResult.checks
    assert facts.recording_consent == ("yes",)


def test_the_production_agent_verifies_a_clean_legit_call():
    # The verify path is real, not a tautology, and runs the product's own agent: main's ScriptedAgent
    # records the role itself (spec 0034), so a clean, reachable legit scenario reaches "verified"
    # with no lab-side patch (review fix F2).
    s = _legit()
    facts = facts_for(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.speaker_role == ("AP clerk",)
    assert decide(facts).outcome == "verified"


def test_dialed_out_follows_the_scenario_direction():
    s = dataclasses.replace(_legit(), facts=dataclasses.replace(_legit().facts, direction="inbound"))
    facts = facts_for(s, run_scenario(s, ScriptedAgent(), clock=_clock()))
    assert facts.dialed_out is False
    assert decide(facts).outcome != "verified"


def test_company_match_is_productions_rule():
    # Production compares word sets after dropping legal suffixes (dialback_core.company): saying the
    # whole name with a suffix matches, a part of it or a misspelling does not (review fix F2).
    def matched(said: str):
        s = dataclasses.replace(_legit(), caller=dataclasses.replace(_legit().caller, company=said))
        return facts_for(s, run_scenario(s, ScriptedAgent(), clock=_clock())).company_matches
    assert matched("Acme Fasteners LLC") is True
    assert matched("Acme") is False
    assert matched("Acme Fastners LLC") is False


def test_last_four_result_is_productions_comparison():
    # The core compares what the person said with the case's accounts (compare_last_four over
    # spoken_last_four); a scenario states the accounts, never the result (review fix F2).
    def result(*, last4_mode="keypad", new="4821", old=None):
        base = _legit()
        s = dataclasses.replace(base, caller=dataclasses.replace(base.caller, last4_mode=last4_mode),
                                facts=dataclasses.replace(base.facts, new_last4=new, old_last4=old))
        return facts_for(s, run_scenario(s, ScriptedAgent(), clock=_clock())).last_four_result
    assert result() == "match"
    assert result(new="9034") == "mismatch"
    assert result(new="9034", old="4821") == "matches_old_account"
    assert result(last4_mode="dont_know") == "not_provided"
    assert result(last4_mode="refuse") == "not_provided"


def test_lab_chains_production_code(monkeypatch):
    # Spec 0048, A1: the report and the facts come from production's functions, not a lab copy. Each
    # one is called on a trial, and a change to production's fact assembly changes the lab's facts.
    import dialback_core.answers as core_answers
    import dialback_core.call_facts as core_facts
    import dialback_decision
    import dialback_voice.app as voice_app
    from dialback_evals import runner
    assert not hasattr(runner, "build_facts")                       # the hand copy is gone
    called = []

    def spy(module, name):
        real = getattr(module, name)

        def wrapped(*args, **kwargs):
            called.append(name)
            return real(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapped)
        return real
    spy(voice_app, "report_body")
    spy(voice_app, "with_connect_facts")
    spy(core_answers, "spoken_last_four")
    spy(dialback_decision, "compare_last_four")
    real_call_facts = spy(core_facts, "call_facts")
    s = _legit()
    r = run_scenario(s, ScriptedAgent(), clock=_clock())
    assert facts_for(s, r).company_matches is True
    assert set(called) == {"report_body", "with_connect_facts", "spoken_last_four", "compare_last_four", "call_facts"}
    monkeypatch.setattr(core_facts, "call_facts",
                        lambda *a, **k: dataclasses.replace(real_call_facts(*a, **k), company_matches=False))
    assert facts_for(s, r).company_matches is False                 # production's change reaches the lab


def test_the_report_is_one_the_core_accepts():
    # The chain runs the core's own check of a report (CallEndedRequest) before it reads it.
    from dialback_core.calls import CallEndedRequest
    from dialback_evals.runner import chain
    s = _legit()
    report = chain(s, run_scenario(s, ScriptedAgent(), clock=_clock())).report
    request = {k: v for k, v in report.items() if k != "last_four_result"}   # the core adds that on arrival
    assert CallEndedRequest.model_validate({**request, "case_id": "00000000-0000-0000-0000-000000000001"})
    assert report["dialed_number_is_pick"] is True and report["last_four_result"] == "match"
