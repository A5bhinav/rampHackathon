"""Spec 0026: the vendor risk list, and spec 0104: one vendor's history, read under a signed-in
person's connection."""
import asyncio
import datetime as dt
import json
import uuid

import psycopg
from dialback_ledger.demo import load, load_seed
from dialback_ledger.risk import STATUS_TEXT, risk_list, vendor_ledger
from psycopg.rows import dict_row
from test_demo import demo_dataset
from test_store import load as load_truth
from test_store import new_vendor, truth_of


def as_person(url: str, dataset_id, read=risk_list, user=None, **kw):
    """Read the way the core's person_tx does: role authenticated, with a user's claims."""
    async def go():
        async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn, conn.transaction():
            await conn.execute("set local role authenticated")
            await conn.execute("select set_config('request.jwt.claims', %s, true)",
                               (json.dumps({"sub": str(user or uuid.uuid4()), "role": "authenticated"}),))
            return await read(conn, dataset_id, **kw)
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


# Spec 0104: one vendor's history ------------------------------------------------------------------

DOC_KEYS = {"document_id", "kind", "custody_channel", "received_at", "claimed_date", "sender", "subject", "thread_id",
            "parent_document_id", "observations"}


def ledger_of(url, dataset_id, vendor_id, now, user=None):
    return as_person(url, dataset_id, vendor_ledger, user, vendor_id=uuid.UUID(str(vendor_id)), now=now)


def test_vendor_ledger_agrees_with_the_risk_list_and_stores_nothing(database_url):
    d = demo_dataset(database_url)
    asyncio.run(load(database_url, phones={}))
    now = dt.datetime.now(dt.UTC)
    count = "select (select count(*) from ledger.contact_points) + (select count(*) from ledger.number_selections) as n"
    with psycopg.connect(database_url, row_factory=dict_row) as c:
        before = c.execute(count).fetchone()["n"]
    rows = as_person(database_url, d["dataset_id"], now=now)
    assert len(rows) == 3
    for row in rows:
        v = ledger_of(database_url, d["dataset_id"], row["vendor_id"], now)
        assert v["risk"] == row                                   # the same run, the same now
        rule = v["rule"]
        base = {"rule_version", "rule_settings", "as_of", "cutoff", "outcome", "candidates"}
        assert set(rule) == base | ({"contact_point_id", "alternates"} if row["provable_number"] else set())  # no case or selection id
        assert rule["rule_version"] == "ledger-select-v1" and rule["as_of"].startswith(now.strftime("%Y-%m-%dT%H:%M"))
        pick = [c for c in rule["candidates"] if c.get("rank") == 1]
        if row["provable_number"]:
            assert rule["outcome"] == "picked" and pick[0]["e164"] == row["provable_number"]
            assert rule["contact_point_id"] == pick[0]["contact_point_id"]
            ranked = sorted((c for c in rule["candidates"] if c["eligible"]), key=lambda c: c["rank"])[1:3]
            assert rule["alternates"] == [{k: c[k] for k in ("contact_point_id", "e164", "source_types")} for c in ranked]
        else:
            assert rule["outcome"] == "no_provable_number" and not pick and "contact_point_id" not in rule
        assert {c["e164"] for c in rule["candidates"] if c["on_file"]} >= ({row["number_on_file"]} - {None})
    with psycopg.connect(database_url, row_factory=dict_row) as c:
        assert c.execute(count).fetchone()["n"] == before          # reading stores nothing


def test_vendor_ledger_lists_every_document_with_its_phone_numbers(database_url):
    d = demo_dataset(database_url)
    asyncio.run(load(database_url, phones={}))
    acme = next(r for r in as_person(database_url, d["dataset_id"]) if r["legal_name"] == load_seed()["vendors"]["acme"]["name"])
    v = ledger_of(database_url, d["dataset_id"], acme["vendor_id"], None)
    with psycopg.connect(database_url, row_factory=dict_row) as c:
        phones = {str(r["document_id"]): r["n"] for r in c.execute(
            "select d.id as document_id, count(o.id) filter (where o.kind = 'phone') as n from ledger.documents d "
            "left join ledger.observations o on o.document_id = d.id where d.dataset_id = %s and d.vendor_id = %s group by d.id",
            (d["dataset_id"], acme["vendor_id"])).fetchall()}
    docs = v["documents"]
    assert v["document_count"] == len(docs) == len(phones) == len(load_seed()["vendors"]["acme"]["documents"])
    assert {x["document_id"]: len(x["observations"]) for x in docs} == phones
    assert all(set(x) == DOC_KEYS for x in docs)
    assert [x["received_at"] for x in docs] == sorted((x["received_at"] for x in docs), reverse=True)  # newest first
    at = {x["document_id"]: i for i, x in enumerate(docs)}
    children = [x for x in docs if x["parent_document_id"]]
    assert children and all(at[x["parent_document_id"]] < at[x["document_id"]] for x in children)       # email, then attachments
    seen = [o for x in docs for o in x["observations"]]
    assert {o["e164"] for o in seen} == {c["e164"] for c in v["rule"]["candidates"]}
    assert all(set(o) == {"e164", "role", "party", "visibility", "quote"} for o in seen)
    assert all(o["quote"] is None for o in seen if o["visibility"] != "visible")


def test_vendor_ledger_shows_no_bank_account_and_redacts_long_digit_runs(database_url):
    v = new_vendor(database_url, "Quoted account")
    fingerprint = "ab" * 32
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as c:
        doc = c.execute("insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at, subject) "
                        "values (%s, %s, 'invoice', %s, 'email', now() - interval '200 days', 'Pay to 000123456789') returning id",
                        (v["dataset_id"], v["vendor_id"], "0" * 64)).fetchone()["id"]
        for kind, value, quote in (("phone", "+14155550142", "AR line +14155550142, remit to account 000123456789"),
                                   ("bank_account", f"026009593:6789:{fingerprint}", None)):
            c.execute("insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, role, party, "
                      "quote, visibility, extractor_version) values (%s, %s, %s, %s, %s, 'ar', 'vendor', %s, 'visible', 't')",
                      (v["dataset_id"], doc, v["vendor_id"], kind, value, quote))
    out = ledger_of(database_url, v["dataset_id"], v["vendor_id"], None)
    assert out["documents"][0]["subject"] == "Pay to [digits removed]"
    (only,) = out["documents"][0]["observations"]
    assert only == {"e164": "+14155550142", "role": "ar", "party": "vendor", "visibility": "visible",
                    "quote": "AR line +14155550142, remit to account [digits removed]"}
    raw = json.dumps(out)
    assert "000123456789" not in raw and fingerprint not in raw and "026009593" not in raw


def test_vendor_ledger_of_a_vendor_on_another_dataset_is_none(database_url):
    a, b = new_vendor(database_url, "Here"), new_vendor(database_url, "Elsewhere")
    assert ledger_of(database_url, a["dataset_id"], b["vendor_id"], None) is None
    assert ledger_of(database_url, a["dataset_id"], uuid.uuid4(), None) is None
    empty = ledger_of(database_url, a["dataset_id"], a["vendor_id"], None)   # a vendor with no documents yet
    assert empty["documents"] == [] and empty["document_count"] == 0 and empty["rule"]["candidates"] == []
    assert empty["risk"]["status"] == "missing" and empty["rule"]["outcome"] == "no_provable_number"


def test_vendor_ledger_shows_only_rows_recorded_before_now_and_caps_the_list(database_url, monkeypatch):
    d = demo_dataset(database_url)
    asyncio.run(load(database_url, phones={}))
    acme = next(r for r in as_person(database_url, d["dataset_id"]) if r["legal_name"] == load_seed()["vendors"]["acme"]["name"])
    before = ledger_of(database_url, d["dataset_id"], acme["vendor_id"], dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
    assert before["documents"] == [] and before["document_count"] == 0 and before["rule"]["candidates"] == []
    monkeypatch.setattr("dialback_ledger.risk.DOCUMENT_LIMIT", 5)
    capped = ledger_of(database_url, d["dataset_id"], acme["vendor_id"], None)
    assert len(capped["documents"]) == 5 and capped["document_count"] == len(load_seed()["vendors"]["acme"]["documents"])


def test_vendor_ledger_is_none_for_a_dataset_the_person_cant_see(database_url):
    v, demo_account = new_vendor(database_url, "Dev only"), uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("insert into app.demo_accounts (user_id) values (%s)", (demo_account,))
    assert ledger_of(database_url, v["dataset_id"], v["vendor_id"], None) is not None
    assert ledger_of(database_url, v["dataset_id"], v["vendor_id"], None, user=demo_account) is None   # a dev dataset
