"""Spec 0026: the vendor risk list, read under a signed-in person's connection."""
import asyncio
import json
import uuid

import psycopg
from dialback_ledger.demo import load, load_seed
from dialback_ledger.risk import STATUS_TEXT, risk_list
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
    assert set(STATUS_TEXT) == {"near_miss", "unproven", "missing", "provable"}
    assert all("safe" not in t.lower() for t in STATUS_TEXT.values())
    assert STATUS_TEXT["provable"].startswith("Provable") and STATUS_TEXT["unproven"].startswith("Not provable")


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
