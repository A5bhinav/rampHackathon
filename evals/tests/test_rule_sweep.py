"""Spec 0048, A5: removing any one decision rule changes some voice scenario's or generated call report's
outcome, except the rules evals/UNREACHABLE.md lists, each with its reason."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

from dialback_evals import sweep

ROOT = Path(__file__).resolve().parents[2]
UNREACHABLE = ROOT / "evals" / "UNREACHABLE.md"


def _clock():
    box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

    def now():
        box["t"] += dt.timedelta(seconds=10)
        return box["t"]
    return now


@lru_cache(maxsize=1)
def _seen():
    subjects = sweep.scenario_subjects(ROOT / "evals" / "scenarios" / "voice", _clock) + sweep.report_subjects(2000, 0)
    return sweep.sweep(subjects)


def test_every_rule_is_seen_or_listed():
    listed = sweep.listed(UNREACHABLE)
    assert sweep.problems(_seen(), listed) == []
    assert len(_seen()) == 37                                         # the whole table, blocks 0 to 3
    assert sorted(r for r, by in _seen().items() if by) == sorted(set(_seen()) - set(listed))


def test_the_sweep_fails_on_an_unlisted_rule_and_on_a_stale_entry():
    listed = sweep.listed(UNREACHABLE)
    without_v08 = {k: v for k, v in listed.items() if k != "V08_digits_match"}
    assert any(p.startswith("V08_digits_match: removing it changes no outcome") for p in
               sweep.problems(_seen(), without_v08))
    assert _seen()["NV01_vendor_denied_change"]                         # removing NV01 is seen
    stale = {**listed, "NV01_vendor_denied_change": "listed by mistake"}
    assert any(p.startswith("NV01_vendor_denied_change: listed in UNREACHABLE.md, but removing it changes")
               for p in sweep.problems(_seen(), stale))
    assert any("without a reason" in p for p in sweep.problems(_seen(), {**listed, "V08_digits_match": " "}))


def test_the_new_scenarios_each_isolate_their_rule():
    # A5's scenarios for rules an honest scripted call reaches: each fires its rule (lab run's expect_rule
    # gate), and the ones whose removal can change an outcome are seen through them.
    by_scenario = {s.name for by in (_seen()[r] for r in ("NV03_digits_match_old_account", "V01_number_eligible",
                                                           "V05_company_matches", "V07_right_person_confirmed"))
                   for s in by}
    assert {"l19-legit-reads-old-account", "l21-legit-pick-changed", "l22-legit-company-unclear",
            "l23-legit-speaker-unknown"} <= by_scenario
