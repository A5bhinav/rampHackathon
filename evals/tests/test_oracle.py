"""The oracle (spec 0017, Task 4): scenario facts to the required verdict. An attack must never
reach verified; a reachable legit change must verify; anything else requires nothing strict."""
from __future__ import annotations

from dialback_evals.oracle import required
from dialback_evals.scenario import CallerBehavior, DeclaredFacts, Scenario


def _scn(*, is_attack, consent="yes", change="yes", last4_mode="keypad",
         new_last4="4821", dialed_number_is_pick=True, speaker_in_change_thread=False,
         offered_number_from_request=False, company="Acme Fasteners", company_is="vendor") -> Scenario:
    return Scenario(id="s", scenario_class="legit_bank_switch" if not is_attack else "line_takeover",
                    is_attack=is_attack, customer_name="Northwind Traders", vendor_name="Acme Fasteners",
                    caller=CallerBehavior(consent=consent, company=company, department="AP",
                                          name="Jordan Lee", role="AP clerk", change=change,
                                          last4_mode=last4_mode, last4="4821", company_is=company_is),
                    facts=DeclaredFacts(dialed_eligible_number=True, new_last4=new_last4,
                                        dialed_number_is_pick=dialed_number_is_pick,
                                        speaker_in_change_thread=speaker_in_change_thread,
                                        offered_number_from_request=offered_number_from_request))


def test_attack_must_not_verify():
    r = required(_scn(is_attack=True, new_last4="9034"))
    assert r.verdict_must_not_be == ("verified",) and r.verdict_must_be is None


def test_reachable_legit_must_verify():
    assert required(_scn(is_attack=False)).verdict_must_be == "verified"


def test_legit_that_cannot_verify_must_not_verify():
    # Review fix F5: this test used to require nothing here, so a fail-open digits rule passed the lab.
    r = required(_scn(is_attack=False, last4_mode="refuse", new_last4="4821"))
    assert r.verdict_must_be is None and r.verdict_must_not_be == ("verified",)


def test_legit_speaker_from_change_thread_is_not_reachable():
    r = required(_scn(is_attack=False, speaker_in_change_thread=True))
    assert r.verdict_must_be is None        # a legit-but-odd change the facts cannot clear



# Spec 0048, A4: the oracle reads the author's judgment of what the caller said, never the product.

def test_oracle_imports_no_core():
    import ast
    import inspect

    from dialback_evals import oracle
    tree = ast.parse(inspect.getsource(oracle))
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + \
              [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert [m for m in modules if m and m.startswith("dialback_core")] == []


def test_the_oracle_reads_the_authors_company_truth():
    # Production would accept "Acme Fasteners LLC" and reject "Acme"; the oracle follows company_is either way.
    assert required(_scn(is_attack=False, company="Acme", company_is="vendor")).verdict_must_be == "verified"
    assert required(_scn(is_attack=False, company="Acme Fasteners LLC", company_is="partial")).verdict_must_be is None
    assert required(_scn(is_attack=False, company_is="other")).verdict_must_not_be == ("verified",)


def test_company_truth_disagreement_is_reported():
    import datetime as dt

    from dialback_evals.runner import facts_for, run_scenario
    from dialback_evals.scorers import score, scorecard
    from dialback_voice.agent import ScriptedAgent

    def clock():
        box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

        def now():
            box["t"] += dt.timedelta(seconds=10)
            return box["t"]
        return now

    def scored(sid, **kw):
        s = _scn(**kw)
        s = Scenario(**{**s.__dict__, "id": sid})
        r = run_scenario(s, ScriptedAgent(), clock=clock())
        return score(s, r, facts_for(s, r))
    negated = scored("negated", is_attack=True, company="Not Acme Fasteners, this is Globex Logistics",
                     company_is="other")                                     # FINDINGS A10: production accepts it
    agreeing = scored("agreeing", is_attack=False)
    partial = scored("partial", is_attack=False, company="Acme", company_is="partial")
    card = scorecard([negated, agreeing, partial])
    assert card["company_findings"] == [{"scenario": "negated", "truth": "other", "production_matches": True}]
    assert negated.company_finding == "other_but_matched" and agreeing.company_finding is None
