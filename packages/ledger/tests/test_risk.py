"""Spec 0026: the vendor risk list, read under a signed-in person's connection."""
import asyncio
import datetime as dt
import json
import uuid

import psycopg
from _world import source_doc
from dialback_ledger.demo import load, load_seed
from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import StubReader
from dialback_ledger.risk import STATUS_TEXT, risk_list
from dialback_ledger.store import add_vendor_domain, write_ingest
from psycopg.rows import dict_row
from test_demo import demo_dataset
from test_store import load as load_truth
from test_store import new_vendor, truth_of


def as_person(url: str, dataset_id) -> list[dict]:
    """Read the way the core's person_tx does: role authenticated, with a user's claims."""
    async def go():
        async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn, conn.transaction():
            await conn.execute("set local role authenticated")
            await conn.execute("select set_config('request.jwt.claims', %s, true)",
                               (json.dumps({"sub": str(uuid.uuid4()), "role": "authenticated"}),))
            return await risk_list(conn, dataset_id)
    return asyncio.run(go())


def test_the_demo_vendors_show_one_of_each_kind(database_url):
    d = demo_dataset(database_url)
    asyncio.run(load(database_url, phones={}))
    rows = as_person(database_url, d["dataset_id"])
    seed = load_seed()["vendors"]
    by = {r["legal_name"]: r for r in rows}
    acme, bright, cobalt = (by[seed[ref]["name"]] for ref in ("acme", "brightline", "cobalt"))
    assert [r["status"] for r in rows] == ["near_miss", "unproven", "provable"]      # worst first
    assert acme["status"] == "provable" and acme["number_on_file"] == seed["acme"]["phones"]["main"]
    assert acme["provable_number"] == seed["acme"]["phones"]["ar"]
    assert bright["status"] == "unproven" and bright["provable_number"] is None
    assert cobalt["status"] == "near_miss" and cobalt["number_on_file"] == seed["cobalt"]["phones"]["on_file"]
    assert cobalt["provable_number"] == seed["cobalt"]["phones"]["ar"]
    for r in rows:
        assert r["status_text"] == STATUS_TEXT[r["status"]]
        assert set(r) == {"vendor_id", "legal_name", "status", "number_on_file", "provable_number", "status_text"}


def test_a_vendor_without_a_number_on_file_is_missing(database_url):
    v = new_vendor(database_url, "No master record")
    load_truth(database_url, v, truth_of("C01"))       # a history with no vendor-master record
    (row,) = as_person(database_url, v["dataset_id"])
    assert row["status"] == "missing" and row["number_on_file"] is None
    assert row["provable_number"] == truth_of("C01")["expected"]["pick"]


def test_a_dataset_with_no_ledger_documents_is_empty(database_url):
    v = new_vendor(database_url, "Nothing ingested")
    assert as_person(database_url, v["dataset_id"]) == []


def test_status_text_never_says_safe():
    # needs_person joined the statuses with spec 0079 (the review's fix pass).
    assert set(STATUS_TEXT) == {"needs_person", "near_miss", "unproven", "missing", "provable"}
    assert all("safe" not in t.lower() for t in STATUS_TEXT.values())
    assert STATUS_TEXT["provable"].startswith("Provable") and STATUS_TEXT["unproven"].startswith("Not provable")
    assert STATUS_TEXT["needs_person"].startswith("Needs a person")


ON_FILE = "+14155550142"


def withheld_vendor(now: dt.datetime) -> list[dict]:
    """The number on file is provable, and an older provable number was called a bank's line ten days
    ago by a clean listing. A lookalike's email 30 days ago pulls the cutoff back past that listing, so
    the change context now holds the label: the run without it would call the older number, and the
    guard withholds (spec 0079)."""

    def days(n: int) -> dt.datetime:
        return now - dt.timedelta(days=n)

    return [source_doc("vm", "vendor_master", days(500), ["Vendor master: Acme Fasteners", "Phone on file: (415) 555-0142"]),
            source_doc("c1", "contract", days(400), ["Supply agreement: Acme", "Accounts receivable: (415) 555-0142"]),
            source_doc("l1", "listing", days(400), ["Acme Fasteners", "Accounts receivable: (415) 555-0142"]),
            source_doc("c2", "contract", days(600), ["Master agreement: Acme", "Accounts receivable: (303) 555-0101"]),
            source_doc("l2", "listing", days(600), ["Acme Fasteners, east office", "Accounts receivable: (303) 555-0101"]),
            source_doc("lbl", "listing", days(10), ["Acme Fasteners", "Bank verification line: (303) 555-0101"]),
            source_doc("look", "email", days(30), ["Hello from Acme", "Main: (303) 555-0177"], sender="ar@acrne.example",
                       thread="t-look", subject="Hello")]


def test_a_vendor_the_rule_withholds_needs_a_person(database_url, monkeypatch):
    monkeypatch.delenv("DIALBACK_READER", raising=False)
    monkeypatch.delenv("DIALBACK_CONTEXT_GUARD", raising=False)
    v = new_vendor(database_url, "Acme Fasteners")

    async def go():
        async with await psycopg.AsyncConnection.connect(database_url) as conn, conn.transaction():
            await write_ingest(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"],
                               result=ingest_all(withheld_vendor(dt.datetime.now(dt.UTC)), extractor_version="test",
                                                 reader=StubReader()))
            await add_vendor_domain(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"], domain="acme.example")
    asyncio.run(go())
    (row,) = as_person(database_url, v["dataset_id"])
    assert row["status"] == "needs_person" and row["status_text"] == STATUS_TEXT["needs_person"]
    assert row["provable_number"] is None and row["number_on_file"] == ON_FILE
    monkeypatch.setenv("DIALBACK_CONTEXT_GUARD", "off")              # today's rule calls the number on file
    (row,) = as_person(database_url, v["dataset_id"])
    assert row["status"] == "provable" and row["provable_number"] == ON_FILE


def _c(e164, eligible, on_file):
    return {"e164": e164, "eligible": eligible, "on_file": on_file}


def test_status_edge_cases():
    from dialback_ledger.risk import _status
    ar, main, near, far = "+14155550142", "+14155550117", "+14155550143", "+14155550169"
    assert _status([_c(ar, True, True)], ar) == ("provable", ar)                       # the pick is on file
    assert _status([_c(ar, True, False), _c(far, False, True)], ar) == ("unproven", far)
    assert _status([_c(ar, False, False), _c(near, False, True)], None) == ("unproven", near)  # near an unprovable number
    assert _status([_c(ar, True, False)], ar) == ("missing", None)
    # Two numbers on file: the worse one shows, so a near miss is never hidden behind a provable line.
    assert _status([_c(main, True, True), _c(ar, True, False), _c(near, False, True)], ar) == ("near_miss", near)
