"""Spec 0012: the ledger stub returns the selection contract's example (DIALBACK_RESOLVER=stub)."""
import datetime as dt
import json
from pathlib import Path
from uuid import uuid4

import dialback_ledger
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "schemas" / "selection.schema.json").read_text())
EXAMPLE = json.loads((CONTRACTS / "examples" / "selection.picked.json").read_text())
CUTOFF = dt.datetime(2026, 11, 4, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=-8)))


def call(**overrides):
    args = {"case_id": uuid4(), "dataset_id": uuid4(), "vendor_id": uuid4(), "cutoff": CUTOFF} | overrides
    return args, dialback_ledger.select_number_stub(**args)


def test_stub_returns_the_picked_example():
    args, out = call()
    assert out["outcome"] == "picked"
    assert out["case_id"] == str(args["case_id"])
    assert out["dataset_id"] == str(args["dataset_id"])
    assert out["vendor_id"] == str(args["vendor_id"])
    assert out["requested_cutoff"] == out["cutoff"] == "2026-11-04T17:30:00.000000Z"
    assert out["rule_version"] == "ledger-stub-v0"
    replaced = {"case_id", "dataset_id", "vendor_id", "requested_cutoff", "cutoff", "rule_version"}
    assert {k: v for k, v in out.items() if k not in replaced} == {k: v for k, v in EXAMPLE.items() if k not in replaced}


def test_stub_output_matches_the_schema():
    _, out = call()
    assert list(Draft202012Validator(SCHEMA).iter_errors(out)) == []
    json.dumps(out)  # plain JSON, nothing the core can't read


def test_stub_has_the_fields_the_core_reads():
    _, out = call()
    assert {"number_selection_id", "rule_version", "contact_point_id", "source_types"} <= out.keys()


def test_stub_calls_do_not_share_state():
    _, a = call()
    a["candidates"].clear()
    _, b = call()
    assert b["candidates"]


def test_a_naive_cutoff_is_refused():
    try:
        call(cutoff=dt.datetime(2026, 11, 4, 9, 30))
    except ValueError:
        return
    raise AssertionError("a cutoff without a time zone must be refused, not guessed")


def test_change_context_is_unknown():
    out = dialback_ledger.change_context(case_id=uuid4(), dataset_id=uuid4(), vendor_id=uuid4(), cutoff=CUTOFF,
                                         speaker_names=("Jordan Lee",), offered_numbers=("+14155550143",))
    assert out == {}


def test_no_real_select_number_yet():
    # DIALBACK_RESOLVER=ledger must keep failing closed until ledger-select-v1 lands.
    assert not hasattr(dialback_ledger, "select_number")
