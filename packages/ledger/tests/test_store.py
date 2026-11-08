"""Spec 0024: the ledger in the database, on a real migrated Postgres (scripts/db/pgtemp.py)."""
import asyncio
import json
import uuid

import psycopg
import pytest
from _world import ROOT, SCHEMA, at
from dialback_ledger import change_context, select_number
from dialback_ledger.ingest import ingest_all
from dialback_ledger.store import add_vendor_domain, mark_change_request, write_ingest
from jsonschema import Draft202012Validator
from psycopg.rows import dict_row

TRUTHS = sorted((ROOT / "corpus" / "truth").glob("*.json"))


def run(coro):
    return asyncio.run(coro)


def new_vendor(url: str, name: str) -> dict:
    """A dataset with one vendor and a bank change, as the core would record them."""
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
        chain = c.execute("insert into audit.chains (label) values ('ledger-test') returning id").fetchone()["id"]
        ds = c.execute("insert into app.datasets (kind, label, chain_id) values ('dev', 'ledger-test', %s) returning id",
                       (chain,)).fetchone()["id"]
        vendor = c.execute("insert into app.vendors (dataset_id, ramp_vendor_id, legal_name) values (%s, %s, %s) returning id",
                           (ds, f"ven_{uuid.uuid4().hex[:8]}", name)).fetchone()["id"]
        accounts = [c.execute("insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint) "
                              "values (%s, %s, '021000021', %s, %s) returning id", (ds, vendor, last4, uuid.uuid4().hex * 2)
                              ).fetchone()["id"] for last4 in ("1234", "4821")]
        change = c.execute("insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, "
                           "source_event_id) values (%s, %s, %s, %s, 'webhook', %s) returning id",
                           (ds, vendor, accounts[0], accounts[1], f"evt_{uuid.uuid4().hex[:8]}")).fetchone()["id"]
    return {"dataset_id": ds, "vendor_id": vendor, "change_id": change}


def open_case(url: str, v: dict) -> uuid.UUID:
    """Open the case in its own transaction, after the ledger rows, as in production."""
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
        r = c.execute("select app.apply_case_event(null, 'change_detected', '{\"kind\": \"system\", \"id\": null}'::jsonb, %s, %s) as r",
                      (f"{v['change_id']}:detected", json.dumps({"change_event_id": str(v["change_id"])}))).fetchone()["r"]
    return uuid.UUID(r["case_id"])


def load(url: str, v: dict, truth: dict, *, request: bool = True) -> None:
    docs = json.loads((ROOT / "corpus" / truth["documents"]).read_text())
    result = ingest_all(docs, extractor_version="test")

    async def go():
        async with await psycopg.AsyncConnection.connect(url) as conn, conn.transaction():
            await write_ingest(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"], result=result)
            await add_vendor_domain(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"], domain=truth["vendor"]["domain"])
            if request:
                await mark_change_request(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"],
                                          document_id=uuid.UUID(truth["request_document_id"]))
    run(go())


def stored(url: str, selection_id: str) -> dict:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute("select selection from ledger.number_selections where id = %s", (selection_id,)).fetchone()["selection"]


def truth_of(name: str) -> dict:
    return json.loads((ROOT / "corpus" / "truth" / f"{name}.json").read_text())


def pick(url, v, case, truth):
    return run(select_number(case_id=case, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"],
                             cutoff=at(truth["ramp_change_at"]), database_url=url))


@pytest.mark.parametrize("path", TRUTHS, ids=lambda p: p.stem)
def test_gate_every_scenario_through_the_database(database_url, path):
    truth = json.loads(path.read_text())
    v = new_vendor(database_url, truth["vendor"]["name"])
    load(database_url, v, truth)
    case = open_case(database_url, v)
    out = pick(database_url, v, case, truth)
    doc = stored(database_url, out["number_selection_id"])
    assert list(Draft202012Validator(SCHEMA).iter_errors(doc)) == []
    assert doc["outcome"] == truth["expected"]["outcome"]
    assert doc.get("e164") == truth["expected"]["pick"]
    assert out["rule_version"] == "ledger-select-v1"
    assert (out.get("contact_point_id") is not None) == (truth["expected"]["pick"] is not None)


def test_a_pick_stores_every_candidate_and_answers_the_core(database_url):
    truth = truth_of("P01")
    v = new_vendor(database_url, "P01")
    load(database_url, v, truth)
    case = open_case(database_url, v)
    out = pick(database_url, v, case, truth)
    assert set(out) >= {"number_selection_id", "rule_version", "contact_point_id", "source_types"}
    with psycopg.connect(database_url, row_factory=dict_row) as c:
        points = {r["e164"]: r for r in c.execute("select * from ledger.contact_points where vendor_id = %s", (v["vendor_id"],))}
        row = c.execute("select * from ledger.number_selections where case_id = %s", (case,)).fetchone()
    assert set(points) == {p["e164"] for p in truth["planted"]}
    assert str(row["contact_point_id"]) == out["contact_point_id"] and points[truth["expected"]["pick"]]["status"] == "eligible"
    poison = next(p["e164"] for p in truth["planted"] if p["truth"] == "poison")
    assert points[poison]["status"] == "excluded" and "first_seen_in_change_context" in points[poison]["exclusion_reason"]


def test_selecting_again_returns_the_same_and_stores_nothing_new(database_url):
    truth = truth_of("C01")
    v = new_vendor(database_url, "C01")
    load(database_url, v, truth)
    case = open_case(database_url, v)
    first, second = pick(database_url, v, case, truth), pick(database_url, v, case, truth)
    assert first == second
    with psycopg.connect(database_url) as c:
        assert c.execute("select count(*) from ledger.number_selections where case_id = %s", (case,)).fetchone()[0] == 1


def test_without_a_change_request_the_refusal_is_stored(database_url):
    truth = truth_of("C01")
    v = new_vendor(database_url, "C01 without request")
    load(database_url, v, truth, request=False)
    case = open_case(database_url, v)
    out = pick(database_url, v, case, truth)
    assert out.get("contact_point_id") is None and "change request" in out["reason"]
    assert stored(database_url, out["number_selection_id"])["outcome"] == "no_provable_number"


def test_rows_recorded_after_the_case_opened_are_ignored(database_url):
    truth = truth_of("C01")
    v = new_vendor(database_url, "C01 late")
    case = open_case(database_url, v)          # the case opens first...
    load(database_url, v, truth)               # ...so everything the ledger learns comes after it
    out = pick(database_url, v, case, truth)
    assert out.get("contact_point_id") is None


@pytest.mark.parametrize("url", [None, "postgresql://nobody@127.0.0.1:1/none"])
def test_no_database_is_a_refusal_never_an_exception(url, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    out = run(select_number(case_id=uuid.uuid4(), dataset_id=uuid.uuid4(), vendor_id=uuid.uuid4(),
                            cutoff=at("2026-10-01T18:00:00Z"), database_url=url))
    assert out.get("contact_point_id") is None and out.get("number_selection_id") is None and out["reason"]
    assert run(change_context(case_id=uuid.uuid4(), dataset_id=uuid.uuid4(), vendor_id=uuid.uuid4(),
                              cutoff=at("2026-10-01T18:00:00Z"), speaker_names=(), offered_numbers=("+14155550142",),
                              database_url=url)) == {}


def test_change_context_knows_where_an_offered_number_came_from(database_url):
    truth = truth_of("P01")
    v = new_vendor(database_url, "P01 context")
    load(database_url, v, truth)
    case = open_case(database_url, v)
    poison = next(p["e164"] for p in truth["planted"] if p["truth"] == "poison")

    def ctx(*numbers):
        return run(change_context(case_id=case, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"],
                                  cutoff=at(truth["ramp_change_at"]), speaker_names=("Jordan Lee",),
                                  offered_numbers=numbers, database_url=database_url))

    assert ctx("(415) 555-0143") == {"offered_number_from_request": True} == ctx(poison)
    assert ctx(truth["expected"]["pick"]) == {"offered_number_from_request": False}
    assert ctx("+14155550199") == {}                       # never seen: unknown
    assert ctx(truth["expected"]["pick"], poison) == {"offered_number_from_request": True}


def test_the_same_documents_load_into_two_datasets(database_url):
    # Every demo reset makes a new dataset and loads the same files into it.
    truth = truth_of("C02")
    picks = []
    for name in ("first reset", "second reset"):
        v = new_vendor(database_url, name)
        load(database_url, v, truth)
        load(database_url, v, truth)  # loading twice into one dataset adds nothing
        case = open_case(database_url, v)
        picks.append(stored(database_url, pick(database_url, v, case, truth)["number_selection_id"]).get("e164"))
        with psycopg.connect(database_url) as c:
            n = c.execute("select count(*) from ledger.documents where dataset_id = %s", (v["dataset_id"],)).fetchone()[0]
        assert n == len(json.loads((ROOT / "corpus" / truth["documents"]).read_text()))
    assert picks == [truth["expected"]["pick"]] * 2
