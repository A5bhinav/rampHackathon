"""Spec 0079: the change context may cancel a call, but never move it to a different number.

The rule runs today's rule twice, on the case as it is and with the change context's documents
removed, and calls only the start the two call lists share. These worlds are the findings it
fixes (FINDINGS A1, A3, A6), plus the worlds where it must change nothing.
"""
import asyncio
import datetime as dt
import uuid

import psycopg
import pytest
from _world import SCHEMA, at, calls, frozen, in_thread, provable, run, source_doc, world
from dialback_ledger import select_number
from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import StubReader
from dialback_ledger.select import RULE_VERSION, WITHHELD, change_context_documents
from dialback_ledger.store import add_vendor_domain, mark_change_request, write_ingest
from jsonschema import Draft202012Validator
from psycopg.rows import dict_row
from test_store import new_vendor, open_case

REAL, PLANTED = "+14155550142", "+16455550177"      # the vendor's line; a provable number someone planted
X = "+13035550101"                                  # A6: older than the real line, labeled a bank's by a clean listing
A, B, C = "+14155550117", "+14155550169", "+14155550124"
UNRELATED = "+18675550188"
PICK_FIELDS = ("contact_point_id", "e164", "source_types", "alternates")
VERSION_FIELDS = ("rule_version", "number_selection_id", "inputs_sha256")


def without(out: dict, fields) -> dict:
    return {k: v for k, v in out.items() if k not in fields}


def assert_withheld(out: dict, today: dict) -> None:
    """Decision 3 and 4: nobody is called, a person verifies, and every candidate is today's."""
    assert out["outcome"] == "withheld"
    assert out["reason"] == WITHHELD
    assert not set(PICK_FIELDS) & set(out), "a withheld selection names no number"
    assert out["candidates"] == today["candidates"], "withheld changes no candidate's eligibility, rank or reasons"
    assert out["rule_version"] == RULE_VERSION
    assert list(Draft202012Validator(SCHEMA).iter_errors(out)) == []


# The fixes ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(("role", "party"), [("unknown", "bank"), ("fax", "vendor"), ("unknown", "unknown")],
                         ids=["bank-line", "fax-line", "bare-mention"])
def test_change_context_label_is_withheld(role, party):
    # FINDINGS A1: the request's thread calls the real number a bank's line, a fax, or mentions it
    # bare. Today the label vetoes it and the call goes to the planted number.
    w = world(provable(REAL, 200) + provable(PLANTED, 150) + [in_thread(REAL, role=role, party=party)])
    today = frozen(w)
    assert today["e164"] == PLANTED, "the failure this fixes"
    out = run(w)
    assert_withheld(out, today)
    assert PLANTED not in calls(out)


@pytest.mark.parametrize("age", [365, 730])
def test_old_thread_copy_is_withheld(age):
    # FINDINGS A3: an old copy of the real number in the request's thread makes R2 refuse it, with
    # no label at all, and the planted number gets the call.
    w = world(provable(REAL, 200) + provable(PLANTED, 150) + [in_thread(REAL, days=age)])
    today = frozen(w)
    assert today["e164"] == PLANTED, "the failure this fixes"
    assert "first_seen_in_change_context" in next(c for c in today["candidates"] if c["e164"] == REAL)["reasons"]
    out = run(w)
    assert_withheld(out, today)
    assert PLANTED not in calls(out)


def _context_blind(w: dict) -> dict:
    """The obvious fix FINDINGS A6 ruled out: today's rule with the change context removed, alone."""
    today = frozen(w)
    context = {d.id for d in change_context_documents(**w)}
    return frozen(w | {"documents": [d for d in w["documents"] if d.id not in context],
                       "sightings": [s for s in w["sightings"] if s.document_id not in context],
                       "requested_cutoff": at(today["cutoff"])})


def test_lookalike_hole_never_calls_the_plant():
    # FINDINGS A6: X is older than the real line, but a clean listing 20 days back calls it a bank's,
    # so today's rule calls the real line. A lookalike email pulls the cutoff back past that listing,
    # into the change context, where a context-blind rule would ignore the label and call X.
    base = provable(REAL, 200) + [
        {"e164": X, "channel": "contract", "days_before": 400, "visibility": "visible", "role": "ar", "party": "vendor"},
        {"e164": X, "channel": "listing", "days_before": 400, "visibility": "visible", "role": "ar", "party": "vendor"},
        {"e164": X, "channel": "listing", "days_before": 20, "visibility": "visible", "role": "ar", "party": "bank"}]
    assert calls(run(world(base)))[:1] == [REAL] == calls(frozen(world(base)))[:1]
    for days in (30, 100, 400):                       # the lab's lookalike probes (LOOKALIKE_PROBE_DAYS)
        probe = {"e164": X, "channel": "email", "days_before": days, "visibility": "visible", "role": "ar",
                 "party": "vendor", "lookalike": True, "thread": f"t_look{days}"}
        w = world(base + [probe])
        today, out = frozen(w), run(w)
        assert X not in calls(out), days
        if days in (30, 100):
            assert calls(_context_blind(w))[:1] == [X], ("the hole this must not open", days)
            assert today["e164"] == REAL, days       # the known cost: today calls the real line ...
            assert_withheld(out, today)               # ... and the new rule calls no one
        else:
            assert today["outcome"] == out["outcome"] == "no_provable_number", days


def test_context_that_moves_nothing_changes_nothing():
    # A change context that agrees with the call, or talks about other numbers: the selection is
    # today's, apart from the rule's name (and so its id and inputs hash).
    plain = provable(A, 900) + provable(C, 300)
    noisy = plain + [in_thread(A), in_thread(C, days=1, subject="Updated banking details"),
                     {"e164": UNRELATED, "channel": "email", "days_before": 2, "visibility": "visible", "role": "ar",
                      "party": "bank", "lookalike": True, "thread": "t_look"},
                     in_thread(UNRELATED, role="unknown", party="unknown")]
    for rows in (plain, noisy):
        w = world(rows)
        today, out = frozen(w), run(w)
        assert calls(today) == [A, C]
        assert without(out, VERSION_FIELDS) == without(today, VERSION_FIELDS)
        assert out["rule_version"] == RULE_VERSION and today["rule_version"] == "ledger-select-v1"


def test_backups_are_cut_to_the_agreed_prefix():
    # They agree on the pick but not on the backups: the pick is called, the backups only as far as
    # the two lists agree (decision 3). Today the bare mention of B drops it and C moves up.
    w = world(provable(A, 900) + provable(B, 600) + provable(C, 300) + [in_thread(B, role="unknown", party="unknown")])
    today, out = frozen(w), run(w)
    assert calls(today) == [A, C]
    assert out["outcome"] == "picked" and calls(out) == [A]
    assert out["candidates"] == today["candidates"]
    assert without(out, ("alternates", *VERSION_FIELDS)) == without(today, ("alternates", *VERSION_FIELDS))
    assert list(Draft202012Validator(SCHEMA).iter_errors(out)) == []


def test_the_run_without_the_context_keeps_todays_cutoff():
    # Decision 2. FB is a fax line seen 500 days back and FA is one digit off it at 380 days; P is
    # provable at 300 days. A lookalike's email 30 days back pulls today's cutoff to it, where FA and FB
    # are not both 365 days old, so FA is a near miss and today's rule calls P. Rerun at that cutoff,
    # the run without the change context agrees. Rerun at the requested cutoff instead, FA and FB would
    # both be established, FA (older than P) would be eligible, and the case would be withheld.
    fb, fa, p = "+14155550142", "+14155550143", "+13035550101"
    look = {"e164": UNRELATED, "channel": "email", "days_before": 30, "visibility": "visible", "role": "ar",
            "party": "vendor", "lookalike": True, "thread": "t_look"}
    w = world(provable(fb, 500, role="fax") + provable(fa, 380) + provable(p, 300) + [look])
    today, out = frozen(w), run(w)
    assert calls(today)[:1] == [p]
    assert out["outcome"] == "picked" and calls(out) == calls(today)
    context = {d.id for d in change_context_documents(**w)}
    at_requested = frozen(w | {"documents": [d for d in w["documents"] if d.id not in context],
                               "sightings": [s for s in w["sightings"] if s.document_id not in context]})
    assert calls(at_requested)[:1] == [fa], "the world tells the two cutoffs apart"


def test_no_pick_today_stays_no_provable_number():
    # Decision 3: when today's rule picks nothing, the outcome stays no_provable_number with today's reason.
    w = world(provable(REAL, 30) + [in_thread(REAL, party="bank")])
    today, out = frozen(w), run(w)
    assert today["outcome"] == out["outcome"] == "no_provable_number"
    assert out["reason"] == today["reason"]
    assert without(out, VERSION_FIELDS) == without(today, VERSION_FIELDS)
    missing = run(w | {"request": None})
    assert missing["outcome"] == "no_provable_number" and "change request" in missing["reason"]


# The flag (decision 7) ----------------------------------------------------------------------------

def _a1() -> dict:
    return world(provable(REAL, 200) + provable(PLANTED, 150) + [in_thread(REAL, role="unknown", party="bank")])


def test_flag_off_gives_todays_rule_exactly(monkeypatch):
    monkeypatch.setenv("DIALBACK_CONTEXT_GUARD", "off")
    w = _a1()
    assert run(w) == frozen(w)                       # byte for byte: rule_version, id and inputs hash too


@pytest.mark.parametrize("value", [None, "on", "sideways"])
def test_flag_default_and_unknown_values_give_the_new_rule(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DIALBACK_CONTEXT_GUARD", raising=False)
    else:
        monkeypatch.setenv("DIALBACK_CONTEXT_GUARD", value)
    assert run(_a1())["outcome"] == "withheld"


@pytest.mark.parametrize(("value", "version"), [("off", "ledger-select-v1"), (None, RULE_VERSION), ("on", RULE_VERSION)])
def test_a_refusal_names_the_rule_the_flag_runs(monkeypatch, value, version):
    # A refusal before the rule runs (here, no database) names the rule that would have run: with the
    # flag off, today's rule (the review's item 6).
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if value is None:
        monkeypatch.delenv("DIALBACK_CONTEXT_GUARD", raising=False)
    else:
        monkeypatch.setenv("DIALBACK_CONTEXT_GUARD", value)
    got = asyncio.run(select_number(case_id=uuid.uuid4(), dataset_id=uuid.uuid4(), vendor_id=uuid.uuid4(),
                                    cutoff=dt.datetime.now(dt.UTC)))
    assert got.get("contact_point_id") is None, "a refusal never names a number"
    assert got["rule_version"] == version and "no number is provable" in got["reason"]


# Storage (decision 4), on a real migrated Postgres ------------------------------------------------

def a1_documents(now: dt.datetime) -> list[dict]:
    """FINDINGS A1 through real ingestion: the request email's own text calls the real line a bank's."""
    days = lambda n: now - dt.timedelta(days=n)
    return [
        source_doc("real-contract", "contract", days(200), ["Master supply agreement: Acme", "Accounts receivable: (415) 555-0142"]),
        source_doc("real-listing", "listing", days(200), ["Acme Fasteners", "Accounts receivable: (415) 555-0142"]),
        source_doc("planted-contract", "contract", days(150), ["Supply agreement: Acme", "Accounts receivable: (645) 555-0177"]),
        source_doc("planted-listing", "listing", days(150), ["Acme Fasteners, west office", "Accounts receivable: (645) 555-0177"]),
        source_doc("request", "email", now - dt.timedelta(hours=1), ["Please update our bank details for future payments.",
                                                                "Bank verification line: (415) 555-0142"],
             sender="ar@acme.example", thread="thr-request", subject="Updated banking details"),
    ]


def test_withheld_keeps_contact_point_status(database_url, monkeypatch):
    monkeypatch.delenv("DIALBACK_READER", raising=False)       # the stub reader, as ingestion runs by default
    v = new_vendor(database_url, "Acme Fasteners")
    now = dt.datetime.now(dt.UTC)

    async def load():
        async with await psycopg.AsyncConnection.connect(database_url) as conn, conn.transaction():
            await write_ingest(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"],
                               result=ingest_all(a1_documents(now), extractor_version="test", reader=StubReader()))
            await add_vendor_domain(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"], domain="acme.example")
            await mark_change_request(conn, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"], document_id="request")
    asyncio.run(load())
    case = open_case(database_url, v)
    got = asyncio.run(select_number(case_id=case, dataset_id=v["dataset_id"], vendor_id=v["vendor_id"],
                                    cutoff=dt.datetime.now(dt.UTC), database_url=database_url))
    assert got["contact_point_id"] is None and got["reason"] == WITHHELD and got["rule_version"] == RULE_VERSION
    with psycopg.connect(database_url, row_factory=dict_row) as c:
        row = c.execute("select * from ledger.number_selections where id = %s", (got["number_selection_id"],)).fetchone()
        points = {r["e164"]: r for r in c.execute("select * from ledger.contact_points where dataset_id = %s and vendor_id = %s",
                                                  (v["dataset_id"], v["vendor_id"])).fetchall()}
    doc = row["selection"]
    assert row["outcome"] == "withheld" and row["contact_point_id"] is None
    assert list(Draft202012Validator(SCHEMA).iter_errors(doc)) == []
    by_number = {c["e164"]: c for c in doc["candidates"]}
    assert by_number[PLANTED]["eligible"] and by_number[PLANTED]["rank"] == 1     # what today's rule found ...
    assert "not_vendor_party" in by_number[REAL]["reasons"]
    # ... and _store writes each number's vendor-wide status from it, as today (decision 4).
    assert {e: (p["status"], p["exclusion_reason"]) for e, p in points.items()} == {
        e: ("eligible", None) if c["eligible"] else ("excluded", ", ".join(c["reasons"])) for e, c in by_number.items()}
    assert set(points) == {REAL, PLANTED}
    assert uuid.UUID(str(points[PLANTED]["id"])) == uuid.UUID(by_number[PLANTED]["contact_point_id"])
