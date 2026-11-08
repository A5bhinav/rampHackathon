"""Pre-dial checks: before the voice service dials, code confirms the call is allowed. Each failing
check maps to a PreDialRefusal code in internal.yaml; the core then sends the case to review (R7)."""
from __future__ import annotations

import pathlib

import yaml
from dialback_voice.guards import (
    ALL_CODES,
    ATTEMPT_CAP_REACHED,
    CASE_FINISHED,
    CASE_NOT_HELD,
    NOT_ON_DIAL_ALLOWLIST,
    NUMBER_CAP_REACHED,
    NUMBER_NOT_SELECTED,
    NUMBER_OPTED_OUT,
    OUTSIDE_CALLING_WINDOW,
    PreDialInputs,
    pre_dial_refusal,
)


def _ok(**over) -> PreDialInputs:
    base = {"case_exists": True, "case_finished": False, "case_held": True, "has_number_pick": True,
            "number_opted_out": False, "within_calling_window": True, "attempts_used": 0,
            "number_calls_last_30d": 0, "demo_dataset": False, "number_on_allowlist": True}
    return PreDialInputs(**(base | over))


def test_a_good_case_is_allowed():
    assert pre_dial_refusal(_ok()) is None


def test_each_check_returns_its_own_code():
    assert pre_dial_refusal(_ok(case_exists=False)) == CASE_NOT_HELD
    assert pre_dial_refusal(_ok(case_finished=True)) == CASE_FINISHED
    assert pre_dial_refusal(_ok(case_held=False)) == CASE_NOT_HELD
    assert pre_dial_refusal(_ok(has_number_pick=False)) == NUMBER_NOT_SELECTED
    assert pre_dial_refusal(_ok(number_opted_out=True)) == NUMBER_OPTED_OUT
    assert pre_dial_refusal(_ok(within_calling_window=False)) == OUTSIDE_CALLING_WINDOW
    assert pre_dial_refusal(_ok(attempts_used=3)) == ATTEMPT_CAP_REACHED
    assert pre_dial_refusal(_ok(number_calls_last_30d=3)) == NUMBER_CAP_REACHED
    assert pre_dial_refusal(_ok(demo_dataset=True, number_on_allowlist=False)) == NOT_ON_DIAL_ALLOWLIST


def test_allowlist_is_required_only_for_a_demo_dataset():
    assert pre_dial_refusal(_ok(demo_dataset=False, number_on_allowlist=False)) is None
    assert pre_dial_refusal(_ok(demo_dataset=True, number_on_allowlist=True)) is None


def test_a_finished_case_reads_as_finished_not_merely_unheld():
    assert pre_dial_refusal(_ok(case_finished=True, case_held=False)) == CASE_FINISHED


def test_no_number_is_a_harder_stop_than_a_closed_window():
    # R2: without a Trust Ledger pick there is nothing safe to dial, whatever the clock says.
    assert pre_dial_refusal(_ok(has_number_pick=False, within_calling_window=False)) == NUMBER_NOT_SELECTED


def test_the_codes_are_exactly_the_contract_enum():
    root = pathlib.Path(__file__).resolve().parents[3]
    spec = yaml.safe_load((root / "packages/contracts/openapi/internal.yaml").read_text())
    enum = spec["components"]["schemas"]["PreDialRefusal"]["properties"]["code"]["enum"]
    assert set(ALL_CODES) == set(enum)
