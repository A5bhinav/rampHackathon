"""Spec 0083, Task 4: the known-limit frontier and the over-block sweep. Both are measurement; these
tests pin the asserts the spec gives them and the boundary cells most likely to drift."""
from __future__ import annotations

import json

from dialback_evals.frontier import (
    NOISE,
    frontier_table,
    min_effort,
    overblock_holds,
    overblock_sweep,
    overblock_table,
    sweep,
    threshold_holds,
)
from dialback_evals.worldgen import DEFAULT


def test_sweep_writes_table_and_threshold_holds(tmp_path):
    cells = sweep()
    assert threshold_holds(cells, DEFAULT)
    table = frontier_table(cells)
    (tmp_path / "frontier.json").write_text(json.dumps(table))
    assert table["cells"] and "summary" in table["min_effort"]


def test_min_effort_is_derived_from_the_grid():
    me = min_effort(sweep())
    assert me["channels"] >= DEFAULT.min_source_types and me["lead_days"] >= DEFAULT.min_age_days


def test_boundary_cells():
    cells = {(c["channels"], c["lead_days"]): c for c in sweep()}
    assert cells[(1, 365)]["poison_picked"] is False      # one channel is never enough
    assert cells[(2, 60)]["poison_picked"] is False       # under min age is never enough
    assert cells[(2, 90)]["poison_picked"] is True        # exactly at both thresholds: the edge itself


def test_overblock_sweep_baseline_and_inert_noise():
    cells = overblock_sweep()
    assert overblock_holds(cells)                          # baseline picked; hidden and late copies never block
    base = next(c for c in cells if c["noise"] is None)
    assert base["real_picked"] is True and base["real_reasons"] == []
    table = overblock_table(cells)
    assert set(table["summary"]) == set(NOISE) and all(v["line"] for v in table["summary"].values())


# Review finding I6: noise that blocks the real number can move the call to someone else's number.

def test_overblock_records_when_noise_hands_the_pick_to_a_planted_number():
    from dialback_evals.frontier import REAL
    cells = overblock_sweep()
    planted_base = next(c for c in cells if c["planted"] and c["noise"] is None)
    assert planted_base["picked"] == REAL                  # alone, the planted number ranks below the real one
    table = overblock_table(cells)
    assert set(table["planted_summary"]) == set(NOISE)
    assert all(v["line"] for v in table["planted_summary"].values())


def test_overblock_holds_covers_the_planted_rows():
    from dialback_evals.frontier import PLANTED
    cells = overblock_sweep()
    moved = [dict(c, picked=PLANTED, real_picked=False) if c["planted"] and c["noise"] == "hidden_copy" else c
             for c in cells]
    assert overblock_holds(cells) and not overblock_holds(moved)   # hidden text that moved the call must fail


def test_frontier_summary_does_not_claim_the_poison_must_be_older():
    summary = min_effort(sweep())["summary"]
    assert "must also be seen before it" not in summary and "planted" in summary
