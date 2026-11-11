"""Spec 0025: the demo's ledger seed and loader, on a real Postgres."""
import asyncio
import datetime as dt
import json
import uuid

import psycopg
import pytest
from dialback_ledger import demo, select_number
from dialback_ledger.demo import DemoError, load, load_seed, request
from dialback_ledger.llm_reader import for_the_model
from dialback_ledger.reader import StubReader
from dialback_ledger.text import lines, visible_text
from psycopg.rows import dict_row
from test_llm_reader import http_error, like_the_stub, llm_reader, refusal
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


def test_check_refuses_llm_without_its_key(monkeypatch):
    # `dialback-core demo prepare` (spec 0077) calls check() before it resets the demo, so check()
    # makes every refusal load and request make, spec 0049's missing key included. No database.
    monkeypatch.setenv("DIALBACK_READER", "llm")
    with pytest.raises(DemoError, match="ANTHROPIC_API_KEY"):
        demo.check({}, [])
    monkeypatch.setenv("DIALBACK_READER", "stub")
    assert demo.check({"acme": "+12025550100"}, ["acme"]) == {"acme": "+12025550100"}


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


def test_cobalts_ar_line_swaps_too_and_a_reload_reports_it_changed_nothing(database_url):
    d = demo_dataset(database_url)
    def counts():
        with psycopg.connect(database_url) as c:
            return tuple(c.execute(f"select count(*) from ledger.{t} where dataset_id = %s", (d["dataset_id"],)).fetchone()[0]
                         for t in ("observations", "vendor_domains", "documents"))

    first = run(load(database_url, phones={"cobalt": "+12025550100"}, with_requests=True))
    assert first["vendors"]["cobalt"]["phone"] == "team"
    before = counts()
    again = run(load(database_url, phones={"cobalt": "+12025550111"}))       # a different phone, too late
    assert again["vendors"]["cobalt"]["phone"] == "already loaded: unchanged"
    assert counts() == before and before[1] == 3
    assert selection(database_url, d, "cobalt")["e164"] == "+12025550100"


def rows(url: str, dataset_id) -> dict:
    with psycopg.connect(url) as c:
        return {t: c.execute(f"select count(*) from ledger.{t} where dataset_id = %s", (dataset_id,)).fetchone()[0]
                for t in ("documents", "observations", "vendor_domains", "change_requests")}


def calls_the_model(doc: dict) -> bool:
    return for_the_model(visible_text(lines(doc)))[1] > 0


def test_a_failed_document_rolls_back_the_load(database_url, monkeypatch, capsys):
    # Spec 0049, decision 4. The model labels every acme document, then refuses brightline's first,
    # after acme's rows went into the same transaction.
    d = demo_dataset(database_url)
    seed = load_seed()
    acme = [doc for doc in seed["vendors"]["acme"]["documents"] if calls_the_model(doc)]
    failing = next(doc for doc in seed["vendors"]["brightline"]["documents"] if calls_the_model(doc))
    calls = []
    reader = llm_reader(lambda r: refusal(r) if len(calls) > len(acme) else like_the_stub(r), calls)
    monkeypatch.setattr(demo, "reader_for", lambda mode: reader)
    monkeypatch.setenv("DIALBACK_READER", "llm")
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert demo.main(["demo", "load", "--with-requests"]) == 2
    err = capsys.readouterr().err
    assert f"document {failing['id']}" in err and "refusal" in err
    assert len(calls) == len(acme) + 1                     # it stopped at the failed document
    assert rows(database_url, d["dataset_id"]) == {"documents": 0, "observations": 0, "vendor_domains": 0,
                                                   "change_requests": 0}


def test_a_failed_request_writes_nothing(database_url, monkeypatch, capsys):
    # Cobalt's change request is the seed's only one that holds a phone number (the offered line); the
    # others make no model call at all.
    d = demo_dataset(database_url, refs=("cobalt",))
    request_doc = load_seed()["vendors"]["cobalt"]["request"]
    assert calls_the_model(request_doc)
    run(load(database_url, phones={}, reader=StubReader()))      # the history is in; beat 2 hasn't run
    before = rows(database_url, d["dataset_id"])
    monkeypatch.setattr(demo, "reader_for", lambda mode: llm_reader(http_error(500)))
    monkeypatch.setenv("DIALBACK_READER", "llm")
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert demo.main(["demo", "request", "--vendor", "cobalt"]) == 2
    err = capsys.readouterr().err
    assert f"document {request_doc['id']}" in err and "api_error" in err
    assert rows(database_url, d["dataset_id"]) == before and before["change_requests"] == 0


def test_a_retired_demo_dataset_is_refused(database_url):
    old = demo_dataset(database_url)
    demo_dataset(database_url)                                                  # a newer reset retires it
    with pytest.raises(DemoError):
        run(load(database_url, phones={}, dataset_id=old["dataset_id"]))
