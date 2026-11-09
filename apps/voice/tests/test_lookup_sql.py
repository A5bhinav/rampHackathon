"""lookup_call against a real migrated Postgres (spec 0027, decision 6).

Seeds a dataset, a vendor, a case (through app.apply_case_event, the money-path door; app.cases is
never written directly) and a pinned ledger contact point, then asserts the CallDial the Twilio
routes read. Anything short of a complete, picked call resolves to None, so a route never dials or
speaks on a half-known call (R2, R7). The customer and callback come from settings, not the DB."""
from __future__ import annotations

import json
import uuid

import psycopg
from dialback_voice.db import make_lookup
from dialback_voice.store import CallDial
from psycopg.rows import dict_row

CUSTOMER = "Northwind Traders"
CALLBACK = "(415) 555-0100"
E164 = "+14155550123"


def _seed(url: str, *, legal: str = "Acme Fasteners", trade=("Acme",), with_pick: bool = True,
          attempt: int = 2) -> dict:
    """A dataset, vendor and opened case, plus a voice.calls row pinned (or not) to a ledger pick."""
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
        chain = c.execute("insert into audit.chains (label) values ('voice-test') returning id").fetchone()["id"]
        ds = c.execute("insert into app.datasets (kind, label, chain_id) values ('dev','voice-test',%s) returning id",
                       (chain,)).fetchone()["id"]
        vendor = c.execute("insert into app.vendors (dataset_id, ramp_vendor_id, legal_name, trade_names) "
                           "values (%s,%s,%s,%s) returning id",
                           (ds, f"ven_{uuid.uuid4().hex[:8]}", legal, list(trade))).fetchone()["id"]
        acct = [c.execute("insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, "
                          "account_fingerprint) values (%s,%s,'021000021',%s,%s) returning id",
                          (ds, vendor, l4, uuid.uuid4().hex * 2)).fetchone()["id"] for l4 in ("1234", "4821")]
        change = c.execute("insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, "
                           "detected_via, source_event_id) values (%s,%s,%s,%s,'webhook',%s) returning id",
                           (ds, vendor, acct[0], acct[1], f"evt_{uuid.uuid4().hex[:8]}")).fetchone()["id"]
        case = c.execute("select app.apply_case_event(null,'change_detected',"
                         "'{\"kind\":\"system\",\"id\":null}'::jsonb,%s,%s) as r",
                         (f"{change}:detected", json.dumps({"change_event_id": str(change)}))).fetchone()["r"]["case_id"]
        cp = None
        if with_pick:
            cp = c.execute("insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, status) "
                           "values (%s,%s,%s, now(), 'eligible') returning id", (ds, vendor, E164)).fetchone()["id"]
        call = c.execute("insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, contact_point_id, "
                         "agent_version) values (%s,%s,%s,%s,%s,'agent-test') returning id",
                         (ds, case, attempt, f"{case}:call:v1:{attempt}", cp)).fetchone()["id"]
    return {"call_id": call, "case_id": uuid.UUID(str(case)), "attempt": attempt}


def _lookup(url: str, *, customer: str = CUSTOMER, callback: str = CALLBACK):
    return make_lookup(url, customer_name=customer, callback_number=callback)


def test_a_complete_call_resolves_to_a_call_dial(database_url):
    s = _seed(database_url)
    assert _lookup(database_url)(s["call_id"]) == CallDial(
        case_id=s["case_id"], attempt=2, number_e164=E164, customer_name=CUSTOMER,
        callback_number=CALLBACK, vendor_names=("Acme Fasteners", "Acme"))


def test_an_unknown_call_id_is_none(database_url):
    assert _lookup(database_url)(uuid.uuid4()) is None


def test_a_call_with_no_pinned_pick_fails_closed(database_url):
    s = _seed(database_url, with_pick=False)
    assert _lookup(database_url)(s["call_id"]) is None


def test_a_pinned_pick_that_is_gone_fails_closed(database_url):
    # contact_point_id has no FK yet (voice schema, Q40), so a dangling pick is possible; it must
    # still resolve to None rather than dial a number the ledger no longer knows.
    s = _seed(database_url)
    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("update voice.calls set contact_point_id = %s where id = %s", (uuid.uuid4(), s["call_id"]))
    assert _lookup(database_url)(s["call_id"]) is None


def test_an_empty_disclosure_name_fails_closed(database_url):
    # customer_name and callback_number are config; an empty one is caught by dial_from_row (R8, R7).
    s = _seed(database_url)
    assert _lookup(database_url, customer="")(s["call_id"]) is None
    assert _lookup(database_url, callback="")(s["call_id"]) is None
