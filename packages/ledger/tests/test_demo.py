"""Spec 0025: the demo's ledger seed and loader, on a real Postgres."""
import asyncio
import datetime as dt
import json
import uuid

import psycopg
import pytest
from dialback_ledger import select_number
from dialback_ledger.demo import DemoError, load, load_seed, request
from psycopg.rows import dict_row
from test_store import open_case

REFS = ("acme", "brightline", "cobalt")


def run(coro):
    return asyncio.run(coro)


def demo_dataset(url: str, refs=REFS) -> dict:
    """A demo dataset as `demo reset` leaves it: vendors imported, their Ramp ids in demo_refs."""
    seed = load_seed()
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
        c.execute("update app.datasets set retired_at = now() where kind = 'demo' and retired_at is null")
        chain = c.execute("insert into audit.chains (label) values ('demo-test') returning id").fetchone()["id"]
        ds = c.execute("insert into app.datasets (kind, label, chain_id) values ('demo', 'demo-test', %s) returning id",
                       (chain,)).fetchone()["id"]
        ramp = {ref: f"ven_{ref}_{uuid.uuid4().hex[:6]}" for ref in refs}
        vendors = {}
        for ref in refs:
            vendors[ref] = c.execute("insert into app.vendors (dataset_id, ramp_vendor_id, legal_name) values (%s, %s, %s) returning id",
                                     (ds, ramp[ref], seed["vendors"][ref]["name"])).fetchone()["id"]
        c.execute("update app.datasets set demo_refs = %s where id = %s",
                  (json.dumps({"users": {}, "vendors": ramp}), ds))
        changes = {}
        for ref in refs:
            accts = [c.execute("insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint) "
                               "values (%s, %s, '026009593', %s, %s) returning id", (ds, vendors[ref], l4, uuid.uuid4().hex * 2)
                               ).fetchone()["id"] for l4 in ("1111", "0001")]
            changes[ref] = c.execute("insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, "
                                     "detected_via, source_event_id) values (%s, %s, %s, %s, 'webhook', %s) returning id",
                                     (ds, vendors[ref], accts[0], accts[1], f"evt_{uuid.uuid4().hex[:8]}")).fetchone()["id"]
    return {"dataset_id": ds, "vendors": vendors, "changes": changes}


def case_for(url, d, ref):
    return open_case(url, {"dataset_id": d["dataset_id"], "vendor_id": d["vendors"][ref], "change_id": d["changes"][ref]})


def selection(url, d, ref):
    case = case_for(url, d, ref)
    out = run(select_number(case_id=case, dataset_id=d["dataset_id"], vendor_id=d["vendors"][ref],
                            cutoff=dt.datetime.now(dt.UTC), database_url=url))
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute("select selection from ledger.number_selections where id = %s",
                         (out["number_selection_id"],)).fetchone()["selection"]


def test_the_demo_stories(database_url):
    d = demo_dataset(database_url)
    seed = load_seed()
    run(load(database_url, phones={}))                       # the current demo dataset
    for ref in REFS:
        run(request(database_url, vendor=ref))
    acme, bright, cobalt = (selection(database_url, d, ref) for ref in REFS)
    assert acme["outcome"] == "picked" and acme["e164"] == seed["vendors"]["acme"]["phones"]["ar"]
    assert bright["outcome"] == "no_provable_number"
    assert cobalt["outcome"] == "picked" and cobalt["e164"] == seed["vendors"]["cobalt"]["phones"]["ar"]
    offered = seed["vendors"]["cobalt"]["phones"]["offered"]
    refused = next(c for c in cobalt["candidates"] if c["e164"] == offered)
    assert "first_seen_in_change_context" in refused["reasons"]


def test_a_team_phone_replaces_the_ar_line(database_url):
    d = demo_dataset(database_url)
    team = "+12025550100"  # fictional here; on the day, the operator types the team phone
    run(load(database_url, phones={"acme": team}, with_requests=True))
    sel = selection(database_url, d, "acme")
    assert sel["e164"] == team
    fictional = load_seed()["vendors"]["acme"]["phones"]["ar"]
    with psycopg.connect(database_url) as c:
        n = c.execute("select count(*) from ledger.observations where vendor_id = %s and value_normalized = %s",
                      (d["vendors"]["acme"], fictional)).fetchone()[0]
    assert n == 0, "the fictional AR line is gone everywhere the rule reads"


def test_loading_and_requesting_twice_adds_nothing(database_url):
    d = demo_dataset(database_url, refs=("acme",))
    for _ in range(2):
        run(load(database_url, phones={}, with_requests=True))
        run(request(database_url, vendor="acme"))
    with psycopg.connect(database_url) as c:
        docs = c.execute("select count(*) from ledger.documents where dataset_id = %s", (d["dataset_id"],)).fetchone()[0]
        reqs = c.execute("select count(*) from ledger.change_requests where dataset_id = %s", (d["dataset_id"],)).fetchone()[0]
    assert docs == len(load_seed()["vendors"]["acme"]["documents"]) + 1 and reqs == 1  # the history and the request


@pytest.mark.parametrize("problem", ["bad phone", "unknown ref", "no demo dataset", "no demo refs"])
def test_refusals_write_nothing(database_url, problem):
    d = demo_dataset(database_url)
    kwargs = {"phones": {}}
    if problem == "bad phone":
        kwargs["phones"] = {"acme": "call me"}
    elif problem == "unknown ref":
        kwargs["phones"] = {"zenith": "+12025550100"}
    elif problem == "no demo dataset":
        kwargs["dataset_id"] = uuid.uuid4()
    else:
        with psycopg.connect(database_url, autocommit=True) as c:
            c.execute("update app.datasets set demo_refs = null where id = %s", (d["dataset_id"],))
    with pytest.raises(DemoError):
        run(load(database_url, **kwargs))
    with pytest.raises(DemoError):
        run(request(database_url, vendor="zenith"))
    with psycopg.connect(database_url) as c:
        assert c.execute("select count(*) from ledger.documents where dataset_id = %s", (d["dataset_id"],)).fetchone()[0] == 0


def test_whoever_answers_the_team_phone_is_not_from_the_request(database_url):
    from dialback_ledger import change_context
    d = demo_dataset(database_url)
    run(load(database_url, phones={}, with_requests=True))

    def speaker(ref, name):
        case = case_for(database_url, d, ref) if ref not in opened else opened[ref]
        opened[ref] = case
        return run(change_context(case_id=case, dataset_id=d["dataset_id"], vendor_id=d["vendors"][ref],
                                  cutoff=dt.datetime.now(dt.UTC), speaker_names=(name,), offered_numbers=(),
                                  database_url=database_url)).get("speaker_in_change_thread")

    opened = {}
    assert speaker("acme", "Jordan Lee") is False          # Verified becomes possible (V07)
    assert speaker("acme", "Dana Whitfield") is True       # the requester on the line is caught
    assert speaker("cobalt", "Morgan Pryce") is True
