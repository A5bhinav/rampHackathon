"""The Postgres CallStore and lookup_call against a real migrated database (spec 0029).

A case is seeded the way the core drives it: change_detected, hold_confirmed, number_resolved, so
it is `calling` with a confirmed hold, plus the Trust Ledger rows the pick lives in. The seeding
reuses the SQL tests' helpers (supabase/tests/_helpers.sql), which live in this session's pg_temp.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from pathlib import Path

import psycopg
import pytest
from dialback_voice.db import make_lookup
from dialback_voice.guards import pre_dial_refusal
from dialback_voice.pg_store import PgCallStore
from dialback_voice.store import CallDial
from psycopg.rows import dict_row

HELPERS = Path(__file__).resolve().parents[3] / "supabase" / "tests" / "_helpers.sql"
TEAM_PHONE = "+14155550123"
MONDAY_NOON_PT = dt.datetime(2026, 11, 9, 20, 0, tzinfo=dt.UTC)   # Mon 12:00 in Los Angeles
SUNDAY_NOON_PT = dt.datetime(2026, 11, 8, 20, 0, tzinfo=dt.UTC)


@pytest.fixture
def seed(database_url):
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(HELPERS.read_text())
        yield lambda **kw: _seed(conn, **kw)


def _event(conn, case_id, event, key, body):
    return conn.execute("select app.apply_case_event(%s, %s, pg_temp.sys(), %s, %s) as r",
                        (case_id, event, key, json.dumps(body))).fetchone()["r"]


def _seed(conn, *, kind="demo", e164=TEAM_PHONE, status="eligible", pick=True, until="calling"):
    """A case taken as far as `until` ('open' or 'calling'), and its pick unless pick=False."""
    f = conn.execute("select * from pg_temp.setup_fixture()").fetchone()
    conn.execute("update app.datasets set kind = %s where id = %s", (kind, f["dataset"]))
    tag = uuid.uuid4().hex[:8]
    case_id = uuid.UUID(_event(conn, None, "change_detected", f"{tag}:detected", {"change_event_id": str(f["change1"])})["case_id"])
    cp = conn.execute(
        "insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, status, exclusion_reason) "
        "values (%s, %s, %s, now() - interval '400 days', %s, %s) returning id",
        (f["dataset"], f["vendor"], e164, status, None if status == "eligible" else "opted out")).fetchone()["id"]
    if until == "calling":
        _event(conn, case_id, "hold_confirmed", f"{tag}:hold", {"readback_ok": True})
        _event(conn, case_id, "number_resolved", f"{tag}:resolved", {"contact_point_id": str(cp)})
    if pick:
        sel = uuid.uuid4()
        doc = {"number_selection_id": str(sel), "case_id": str(case_id), "dataset_id": str(f["dataset"]),
               "vendor_id": str(f["vendor"]), "outcome": "picked", "rule_version": "ledger-select-v1",
               "inputs_sha256": "0" * 64, "contact_point_id": str(cp), "e164": e164}
        conn.execute(
            "insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, "
            "rule_version, inputs_sha256, selection, recorded_at) values (%s, %s, %s, %s, %s, 'picked', "
            "'ledger-select-v1', %s, %s, now())",
            (sel, f["dataset"], case_id, f["vendor"], cp, "0" * 64, json.dumps(doc)))
    case = conn.execute("select version, call_attempts, dataset_id from app.cases where id = %s", (case_id,)).fetchone()
    return {"case_id": case_id, "cp": cp, "conn": conn, **case}


def _store(url, *, allow=(TEAM_PHONE,), now=MONDAY_NOON_PT) -> PgCallStore:
    return PgCallStore(url, dial_allowlist=frozenset(allow), calling_tz="America/Los_Angeles", now=lambda: now)


def _key(s, attempt=None, version=None):
    return f"{s['case_id']}:call:v{s['version'] if version is None else version}:{attempt or s['call_attempts']}"


def test_a_dialable_case_passes_and_is_recorded_once(seed, database_url):
    s = seed()
    store = _store(database_url)

    async def run():
        pre = await store.gather_pre_dial(case_id=s["case_id"], attempt=s["call_attempts"])
        assert pre_dial_refusal(pre.inputs) is None, pre.inputs
        assert pre.contact_point_id == s["cp"] and pre.number_e164 == TEAM_PHONE
        assert pre.inputs.demo_dataset and pre.inputs.attempts_used == 0
        placed, created = await store.record_call(case_id=s["case_id"], attempt=s["call_attempts"], idempotency_key=_key(s),
                                                  contact_point_id=pre.contact_point_id, number_e164=pre.number_e164,
                                                  agent_version="agent-test")
        again, created_again = await store.record_call(case_id=s["case_id"], attempt=s["call_attempts"], idempotency_key=_key(s),
                                                       contact_point_id=pre.contact_point_id, number_e164=pre.number_e164,
                                                       agent_version="agent-test")
        assert created and not created_again and again == placed, "a replay returns the first call"
        assert await store.find_call(_key(s)) == placed
        assert await store.find_call("no-such-key") is None
        return placed

    placed = asyncio.run(run())
    row = s["conn"].execute("select * from voice.calls where id = %s", (placed.call_id,)).fetchone()
    assert row["dataset_id"] == s["dataset_id"] and row["contact_point_id"] == s["cp"]
    assert row["status"] == "placing" and row["provider"] == "text" and row["agent_version"] == "agent-test"


def test_each_refusal_comes_from_the_data(seed, database_url):
    def refusal(s, **store_kw):
        store = _store(database_url, **store_kw)
        pre = asyncio.run(store.gather_pre_dial(case_id=s["case_id"], attempt=s["call_attempts"]))
        return pre_dial_refusal(pre.inputs)

    assert refusal({"case_id": uuid.uuid4(), "call_attempts": 1}) == "case_not_held", "an unknown case"
    assert refusal(seed(until="open")) == "case_not_held", "no confirmed hold, not calling"
    assert refusal(seed(pick=False)) == "number_not_selected"
    assert refusal(seed(status="excluded")) == "number_opted_out"
    assert refusal(seed(kind="dev"), now=SUNDAY_NOON_PT) == "outside_calling_window"
    assert refusal(seed(kind="demo"), now=SUNDAY_NOON_PT) is None, "the core applies the demo window"
    assert refusal(seed(kind="demo"), allow=()) == "not_on_dial_allowlist", "an empty allowlist calls nobody"
    assert refusal(seed(kind="dev"), allow=()) == "not_on_dial_allowlist", "every dataset needs the allowlist"

    used = seed()
    for n in (1, 2, 3):  # three calls already placed for this case version
        used["conn"].execute("insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, contact_point_id, "
                             "agent_version) values (%s, %s, %s, %s, %s, 'a')",
                             (used["dataset_id"], used["case_id"], n, _key(used, n), used["cp"]))
    assert refusal(used) == "attempt_cap_reached"

    capped = seed()
    for n in (1, 2, 3):  # three calls to the same number, from an earlier version of the case
        capped["conn"].execute("insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, contact_point_id, "
                               "agent_version) values (%s, %s, %s, %s, %s, 'a')",
                               (capped["dataset_id"], capped["case_id"], n, _key(capped, n, version=0), capped["cp"]))
    assert refusal(capped) == "number_cap_reached"


def test_dialed_failed_and_lookup(seed, database_url):
    s = seed()
    store = _store(database_url)

    async def place(key):
        placed, _ = await store.record_call(case_id=s["case_id"], attempt=s["call_attempts"], idempotency_key=key,
                                            contact_point_id=s["cp"], number_e164=TEAM_PHONE, agent_version="a")
        return placed

    dialed = asyncio.run(place(_key(s)))
    asyncio.run(store.mark_dialed(dialed.call_id, provider_call_id="CA0123456789"))
    row = s["conn"].execute("select * from voice.calls where id = %s", (dialed.call_id,)).fetchone()
    assert row["provider"] == "twilio" and row["provider_call_id"] == "CA0123456789" and row["status"] == "ringing"
    assert row["started_at"] is not None

    lookup = make_lookup(database_url, customer_name="Northwind Traders", callback_number="+14155550100")
    assert lookup(dialed.call_id) == CallDial(
        case_id=s["case_id"], attempt=s["call_attempts"], number_e164=TEAM_PHONE, customer_name="Northwind Traders",
        callback_number="+14155550100", vendor_names=("Acme Fasteners LLC",), contact_point_id=s["cp"])
    assert lookup(uuid.uuid4()) is None, "an unknown call"
    assert make_lookup(database_url, customer_name="", callback_number="+14155550100")(dialed.call_id) is None, \
        "no customer name configured: the call isn't run (fail closed)"
    assert make_lookup(database_url, customer_name="Northwind Traders", callback_number="")(dialed.call_id) is None
    assert make_lookup("postgresql://nobody@127.0.0.1:1/none", customer_name="N", callback_number="+1")(dialed.call_id) is None, \
        "a database that can't be reached: None, quickly (fail closed)"

    failed = asyncio.run(place(_key(s) + ":again"))
    asyncio.run(store.mark_failed(failed.call_id, "originate_failed"))
    row = s["conn"].execute("select * from voice.calls where id = %s", (failed.call_id,)).fetchone()
    assert row["status"] == "ended" and row["result"] == "failed" and row["reason"] == "originate_failed"
    assert row["ended_at"] is not None


def test_held_means_calling_with_a_confirmed_hold(seed, database_url):
    def refusal(s):
        pre = asyncio.run(_store(database_url).gather_pre_dial(case_id=s["case_id"], attempt=s["call_attempts"]))
        return pre_dial_refusal(pre.inputs)

    no_hold = seed()  # calling, but the hold row was never confirmed (or was released)
    no_hold["conn"].execute("update app.holds set released_at = now() where case_id = %s", (no_hold["case_id"],))
    assert refusal(no_hold) == "case_not_held", "calling without a hold on"
    held_only = seed(until="open")
    _event(held_only["conn"], held_only["case_id"], "hold_confirmed", f"{held_only['case_id']}:hold", {"readback_ok": True})
    assert refusal(held_only) == "case_not_held", "a hold on, but the case isn't calling"


def test_the_newest_selection_wins_and_the_cap_counts_30_days_of_the_number(seed, database_url):
    s = seed()
    conn = s["conn"]
    case = conn.execute("select dataset_id, vendor_id from app.cases where id = %s", (s["case_id"],)).fetchone()
    later = uuid.uuid4()
    doc = {"number_selection_id": str(later), "case_id": str(s["case_id"]), "dataset_id": str(case["dataset_id"]),
           "vendor_id": str(case["vendor_id"]), "outcome": "no_provable_number", "rule_version": "ledger-select-v1",
           "inputs_sha256": "1" * 64, "contact_point_id": None}
    conn.execute("insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, "
                 "rule_version, inputs_sha256, selection, recorded_at) values (%s, %s, %s, %s, null, 'no_provable_number', "
                 "'ledger-select-v1', %s, %s, now() + interval '1 minute')",
                 (later, case["dataset_id"], s["case_id"], case["vendor_id"], "1" * 64, json.dumps(doc)))
    pre = asyncio.run(_store(database_url).gather_pre_dial(case_id=s["case_id"], attempt=s["call_attempts"]))
    assert pre_dial_refusal(pre.inputs) == "number_not_selected", "a later no-number selection replaces the pick"

    t = seed()
    other = seed()  # another case in another dataset: its calls never count against this number
    for n, age in ((1, "40 days"), (2, "40 days"), (3, "40 days"), (1, "1 day"), (2, "1 day")):
        t["conn"].execute("insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, contact_point_id, "
                          "agent_version, created_at) values (%s, %s, %s, %s, %s, 'a', now() - %s::interval)",
                          (t["dataset_id"], t["case_id"], n, f"{t['case_id']}:call:v0:{n}:{age}", t["cp"], age))
    pre = asyncio.run(_store(database_url).gather_pre_dial(case_id=t["case_id"], attempt=t["call_attempts"]))
    assert pre.inputs.number_calls_last_30d == 2, "calls older than 30 days don't count"
    pre = asyncio.run(_store(database_url).gather_pre_dial(case_id=other["case_id"], attempt=other["call_attempts"]))
    assert pre.inputs.number_calls_last_30d == 0, "another dataset's contact point is counted apart"
