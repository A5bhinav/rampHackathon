"""Spec 0020: the corpus is deterministic, fictional, complete, and its truth is frozen."""
import hashlib
import json
import re
from pathlib import Path

import build
from world import near_miss

CORPUS = Path(__file__).resolve().parents[1]
FICTIONAL_PHONE = re.compile(r"^\+1[2-9][0-9]{2}55501[0-9]{2}$")
ANY_PHONE_TEXT = re.compile(r"\(?\b([2-9][0-9]{2})\)?[ .-]*([0-9]{3})[ .-]*([0-9]{4})\b")


def long_digits(text: str) -> bool:
    """A run of 7+ digits once phone numbers are removed, whatever separates them: an account number."""
    return bool(re.search(r"\d{7,}", re.sub(r"[ .\-]", "", ANY_PHONE_TEXT.sub("", text))))


def test_long_digits():
    assert long_digits("Acct 0001-2345-67") and long_digits("acct 000.123.4567.8")
    assert not long_digits("AR: (415) 555-0142, invoice 1042, ending 4821")


def truths() -> dict[str, dict]:
    return {p: json.loads(c) for p, c in build.build().items() if p.startswith("truth/") and p.endswith(".json")}


def documents() -> dict[str, list[dict]]:
    return {p: json.loads(c) for p, c in build.build().items() if p.endswith("documents.json")}


def test_build_is_deterministic():
    assert build.build() == build.build()


def test_committed_corpus_matches_the_generator():
    assert build.check() == []


def test_the_truth_is_frozen():
    manifest = (CORPUS / "truth" / "manifest.sha256").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == build.FROZEN_TRUTH_SHA256
    names = set()
    for line in manifest.decode().splitlines():
        digest, name = line.split("  ")
        names.add(name)
        assert hashlib.sha256((CORPUS / name).read_bytes()).hexdigest() == digest, name
    assert {p for p in build.build() if p != "truth/manifest.sha256"} == names, "the manifest covers every file"


def test_twelve_patterns_five_controls_and_the_known_limit():
    ids = {t["id"] for t in truths().values()}
    patterns = {i[:3] for i in ids if i.startswith("P")}
    assert patterns == {f"P{n:02d}" for n in range(1, 13)}
    assert {"P04a", "P04b", "P04c"} <= ids
    assert {i for i in ids if i.startswith("C")} == {f"C{n:02d}" for n in range(1, 6)}
    assert "K01" in ids


def test_truth_lists_every_sighting_and_an_expectation():
    for t in truths().values():
        exp = t["expected"]
        assert exp["outcome"] in ("picked", "no_provable_number"), t["id"]
        assert (exp["pick"] is not None) == (exp["outcome"] == "picked"), t["id"]
        assert exp["cutoff"] <= t["request_received_at"] <= t["ramp_change_at"], t["id"]
        for planted in t["planted"]:
            assert planted["truth"] in ("real", "poison")
            for s in planted["sightings"]:
                assert {"document_id", "channel", "received_at", "visibility", "role", "party"} <= s.keys()


def test_poison_is_never_expected_to_be_picked_except_the_known_limit():
    for t in truths().values():
        poison = {p["e164"] for p in t["planted"] if p["truth"] == "poison"}
        if t["kind"] == "poison":
            assert poison, f"{t['id']} plants no poison"
            assert t["expected"]["pick"] not in poison, t["id"]
            assert poison <= t["expected"]["rejected"].keys(), f"{t['id']}: every poisoned number needs its reasons"
        elif t["kind"] == "known_limit":
            assert t["expected"]["pick"] in poison
        else:
            assert not poison, f"control {t['id']} plants poison"


def test_only_fictional_values():
    for t in truths().values():
        for p in t["planted"]:
            assert FICTIONAL_PHONE.match(p["e164"]), p["e164"]
    for path, docs in documents().items():
        for d in docs:
            for addr in filter(None, [d["custody"]["sender"]]):
                assert addr.endswith(".example"), (path, addr)
            for page in d["pages"]:
                for span in page["spans"]:
                    for m in ANY_PHONE_TEXT.finditer(span["text"]):
                        assert m.group(2) == "555" and m.group(3).startswith("01"), (path, span["text"])
                    for dom in re.findall(r"[a-z0-9-]+\.[a-z]{2,}\b", span["text"].lower()):
                        assert dom.endswith(".example"), (path, span["text"])
                    assert not long_digits(span["text"]), (path, span["text"])
            for field in ("sender", "subject", "thread_id"):
                assert not long_digits(d["custody"][field] or ""), (path, field)


def test_a_vendors_real_numbers_are_not_near_misses():
    for t in truths().values():
        real = [p["e164"] for p in t["planted"] if p["truth"] == "real"]
        pairs = [(a, b) for i, a in enumerate(real) for b in real[i + 1:] if near_miss(a, b)]
        assert pairs == [] or t["id"] == "C02", (t["id"], pairs)


def test_near_miss():
    assert near_miss("+14155550142", "+14155550143")  # one digit off
    assert near_miss("+14155550142", "+14155550124")  # two swapped digits
    assert not near_miss("+14155550142", "+14155550117")
    assert not near_miss("+14155550142", "+14155550142")


def test_attachments_have_their_emails_custody():
    for docs in documents().values():
        by_id = {d["id"]: d for d in docs}
        for d in docs:
            parent = d["custody"]["parent_document_id"]
            if parent:
                p = by_id[parent]["custody"]
                assert (d["custody"]["channel"], d["custody"]["received_at"]) == (p["channel"], p["received_at"])
