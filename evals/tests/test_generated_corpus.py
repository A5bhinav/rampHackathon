"""Spec 0083, Task 3: a violating world is frozen as a named case, a frozen case round-trips exactly,
and every case the lab has frozen is re-run against the real rule (a found case stays found)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from pathlib import Path

import pytest
from dialback_evals.bridge import world_from_documents
from dialback_evals.gencorpus import freeze, fuzz, load_all, load_world, rerun, world_digest
from dialback_evals.worldgen import NumberSpec, SightingSpec, World, make_world, run
from dialback_ledger.reader import StubReader

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "evals" / "corpus-generated"
FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")   # NANP 555-0100 to 555-0199, reserved for fiction
E164 = re.compile(r"\+1\d{10}")
HARD = NumberSpec("+14695550199", [], 0, is_poison=True, sightings=(
    SightingSpec("email", 30, sender="lookalike", recorded_late=True),
    SightingSpec("listing", 200, visibility="tiny", shared_id=True),
    SightingSpec("email", 90, sender="subdomain", subject="re")))


def _round_trips(back, w) -> bool:
    """Did a reload give back the same world? Compared field for field on the inputs, not on the rule's
    output, which drops late records before it hashes (review finding I3)."""
    return back.kwargs == w.kwargs and back.poison == w.poison and back.settings == w.settings


def test_frozen_world_round_trips(tmp_path):
    for i, present in enumerate((True, False)):   # with and without a change request
        w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500), HARD],
                       request_present=present)
        case = freeze(w, f"round-trip-{i}", tmp_path, labels=["example"], why="a round trip")
        assert _round_trips(load_world(case), w)


def test_round_trip_check_sees_a_lost_late_record():
    # Review finding I3: the rule drops late records before it hashes, so comparing outputs cannot see
    # a reload that lost one, which is the very field world.json exists to keep.
    w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500), HARD])
    opened = w.kwargs["case_opened_at"]
    lossy = dataclasses.replace(w, kwargs={**w.kwargs, "documents": [d for d in w.kwargs["documents"]
                                                                     if d.recorded_at < opened]})
    assert run(lossy) == run(w)                    # the rule itself cannot tell the two apart
    assert not _round_trips(lossy, w)              # the round-trip check must


def test_freeze_never_replaces_a_different_world(tmp_path):
    # Review finding I2: a frozen case is evidence; nothing may overwrite it with another world.
    a = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)])
    b = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 400)])
    freeze(a, "case", tmp_path, labels=["first"])
    freeze(a, "case", tmp_path, labels=["refreshed"])        # the same world again changes nothing (K3)
    with pytest.raises(FileExistsError):
        freeze(b, "case", tmp_path)
    assert load_world(tmp_path / "case").kwargs == a.kwargs


def test_fuzz_names_a_case_by_its_content(tmp_path, monkeypatch):
    from dialback_evals.gencorpus import world_digest
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["M_reorder"])
    fuzz(examples=25, seed=0, out_dir=tmp_path)
    [(label, world)] = load_all(tmp_path)
    assert label == f"fuzz-seed0-m-reorder-{world_digest(world)[:8]}"


def test_freeze_refuses_a_real_looking_number(tmp_path):
    # Review finding C1: a real-looking number must never reach a committed fixture, whatever produced it.
    real_looking = "+1201" + "5551234"   # built in pieces so the file scan below does not see it
    with pytest.raises(ValueError, match="real-looking"):
        freeze(make_world(numbers=[NumberSpec(real_looking, ["contract", "listing"], 500)]), "x", tmp_path)
    assert load_all(tmp_path) == []


def test_lab_files_hold_only_fictional_numbers():
    found = {}
    # Spec 0046 adds the tests' recorded fixtures to what the lab writes.
    for path in [*(ROOT / "evals" / "src").rglob("*.py"), *(ROOT / "evals" / "tests").rglob("*.py"),
                 *GENERATED.rglob("*.json"), *(ROOT / "evals" / "tests").rglob("*.json")]:
        bad = sorted({n for n in E164.findall(path.read_text(encoding="utf-8")) if not FICTIONAL.match(n)})
        if bad:
            found[str(path.relative_to(ROOT))] = bad
    assert found == {}, found


def test_fuzz_against_a_broken_rule_freezes_a_case(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["I7_poison_pick_below_bar"])
    assert fuzz(examples=25, seed=0, out_dir=tmp_path) == ["I7_poison_pick_below_bar"]
    assert len(load_all(tmp_path)) == 1


def test_fuzz_is_clean_when_nothing_violates(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: [])
    assert fuzz(examples=25, seed=0, out_dir=tmp_path) == []
    assert load_all(tmp_path) == []


def test_rerun_reports_frozen_cases_the_rule_still_fails(tmp_path, monkeypatch):
    freeze(make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)]), "plain", tmp_path)
    assert rerun(tmp_path) == []
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["M_reorder"])
    assert rerun(tmp_path) == [("plain", ["M_reorder"])]


def test_rerun_reports_a_frozen_crash(tmp_path, monkeypatch):
    # Spec 0046: a crash in select() or a check on a frozen case is reported as a label, not raised.
    freeze(make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)]), "plain", tmp_path)

    def crash(world, **_):
        raise ZeroDivisionError("a planted crash")
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", crash)
    assert rerun(tmp_path) == [("plain", ["crash_ZeroDivisionError"])]


@pytest.mark.selection_gate
def test_frozen_findings_rerun_against_the_rule():
    failing = rerun(GENERATED)
    assert failing == [], failing   # each frozen finding must pass on the rule; red until select() is fixed


# Review 2, B1: the production attack as real ingestion shapes it. The frozen case is rebuilt here from
# documents through the ledger's own ingest_all (stub reader), so it cannot drift from what ingestion does.
INGESTED = "planted-number-picked-after-bare-mention-in-request"
REAL_LINE, PLANTED_LINE = "+14155550142", "+16455550177"


def _ingested_world() -> World:
    """The vendor's real number in a contract and a listing 200 days back, a planted number in a contract
    and a listing 150 days back, and a change request whose own body mentions the real number with no
    label the reader can place, the classic wording of a fraudulent request."""
    return _ingest(["Hello, please update our banking details for future payments.",
                    "Please stop using our old number (415) 555-0142."], [])


def _ingested_follow_up_world() -> World:
    """The same records, a plain change request, and a follow-up 20 minutes later from the vendor's own
    (hijacked) mailbox, in a thread and under a subject of its own, naming the real number as a bank
    line: in the change context only because it arrived after the cutoff (re-review K1)."""
    return _ingest(["Hello, please update our banking details for future payments."],
                   [("f1", "email", 0, "ap@acme.example", "t-f1", "One more thing",
                     ("Bank verification line: (415) 555-0142",), 20)])


def _ingest(request_lines: list[str], extra: list[tuple]) -> World:
    t0 = dt.datetime(2026, 10, 1, 16, tzinfo=dt.UTC)

    def doc(doc_id, channel, days, sender, thread, subject, *texts, minutes=0):
        when = (t0 - dt.timedelta(days=days) + dt.timedelta(minutes=minutes)).isoformat()
        spans = [{"text": t, "x": 72, "y": 100 + 20 * n, "size": 11, "color": "#000000"} for n, t in enumerate(texts)]
        return {"id": doc_id, "kind": "email" if channel == "email" else "pdf",
                "custody": {"channel": channel, "received_at": when, "sender": sender, "thread_id": thread,
                            "subject": subject},
                "pages": [{"width": 612, "height": 792, "spans": spans}]}

    documents = [
        doc("c1", "contract", 200, None, None, None, "Accounts receivable: (415) 555-0142"),
        doc("l1", "listing", 200, None, None, None, "Accounts receivable: (415) 555-0142"),
        doc("c2", "contract", 150, None, None, None, "Accounts receivable: (645) 555-0177"),
        doc("l2", "listing", 150, None, None, None, "Accounts receivable: (645) 555-0177"),
        doc("req", "email", 0, "ap@acme.example", "req", "Updated banking details", *request_lines),
        *(doc(i, ch, days, sender, thread, subject, *texts, minutes=minutes)
          for i, ch, days, sender, thread, subject, texts, minutes in extra),
    ]
    # Spec 0052, decision 4: the one bridge from ingestion to select(), with the stub reader named, never
    # taken from the environment (spec 0049, D8).
    return world_from_documents(documents, request_id="req", vendor_domains=("acme.example",), requested_cutoff=t0,
                                case_opened_at=t0 + dt.timedelta(hours=1), reader=StubReader(),
                                extractor_version="lab-0083", poison=frozenset({PLANTED_LINE}))


def test_ingested_case_is_still_what_real_ingestion_produces():
    world = _ingested_world()
    req = world.kwargs["request"]
    assert [(s.e164, s.party) for s in world.kwargs["sightings"] if s.document_id == req.document_id] == [
        (REAL_LINE, "unknown")]                      # the bare mention, in the request email itself
    assert world_digest(world) == world_digest(load_world(GENERATED / INGESTED))


INGESTED_FOLLOW_UP = "planted-number-picked-after-follow-up-email"


def test_ingested_follow_up_case_is_still_what_real_ingestion_produces():
    world = _ingested_follow_up_world()
    assert [(s.e164, s.party) for s in world.kwargs["sightings"] if s.document_id == "f1"] == [(REAL_LINE, "bank")]
    assert world_digest(world) == world_digest(load_world(GENERATED / INGESTED_FOLLOW_UP))



# Re-review K3: a frozen case's notes are its history. Freezing the same world again (which lab fuzz does
# whenever it re-finds a case) leaves them alone; `why` changes only when asked, and `labels` never.

def test_refreezing_the_same_world_keeps_its_notes(tmp_path):
    w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)])
    case = freeze(w, "kept", tmp_path, labels=["M_first"], why="first")
    freeze(w, "kept", tmp_path, labels=["M_second"], why="second")
    data = json.loads((case / "world.json").read_text())
    assert data["labels"] == ["M_first"] and data["why"] == "first"


def test_replace_why_changes_only_why(tmp_path):
    w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)])
    case = freeze(w, "kept", tmp_path, labels=["M_first"], why="first")
    freeze(w, "kept", tmp_path, labels=["M_second"], why="later context", replace_why=True)
    data = json.loads((case / "world.json").read_text())
    assert data["labels"] == ["M_first"] and data["why"] == "later context"


# Spec 0048, C1: `lab fuzz --search first` draws the pinned sample, the same worlds `draws.sample` gives
# at that seed in any process (test_worldgen.py::test_a_seed_pins_the_worlds).
def test_lab_fuzz_draws_the_pinned_sample(tmp_path, monkeypatch):
    from dialback_evals.draws import sample
    from dialback_evals.worldgen import worlds
    seen: list[str] = []

    def record(world):
        seen.append(world_digest(world))
        return []
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", record)
    assert fuzz(examples=40, seed=0, out_dir=tmp_path) == []
    assert seen == [world_digest(w) for w in sample(worlds(), examples=40, seed=0)]
