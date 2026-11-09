"""Spec 0022 A: custody, text, visibility and phone candidates, gated on the frozen corpus."""
import datetime as dt
import json
from collections import Counter

import pytest
from _world import ROOT
from dialback_ledger.candidates import find_phones
from dialback_ledger.custody import custody_for, document_sha256
from dialback_ledger.text import lines

TRUTHS = sorted((ROOT / "corpus" / "truth").glob("*.json"))


def page(*spans, width=612, height=792):
    return {"id": "d1", "kind": "email", "pages": [{"width": width, "height": height, "spans": list(spans)}],
            "custody": {"channel": "email", "received_at": "2026-01-01T00:00:00Z", "sender": "a@x.example",
                        "thread_id": "t", "subject": "s", "parent_document_id": None}}


def span(text, **kw):
    return {"text": text, "x": 72, "y": 72, "size": 10, "color": "#000000"} | kw


def test_visibility():
    doc = page(span("seen"), span("white", color="#FFFFFF"), span("near white", color="#fefefe"),
               span("tiny", size=1), span("right", x=700), span("below", y=900), span("left", x=-40))
    assert [ln.visibility for ln in lines(doc)] == ["visible", "white", "white", "tiny", "off_page", "off_page", "off_page"]


def test_phones_are_found_and_nothing_else():
    doc = page(span("AR: (415) 555-0142, fax 212-555-0169"), span("Invoice 1042 dated 2026-10-01, account ending 4821"),
               span("919.555.0124", color="#ffffff"))
    found = [(c.e164, c.visibility, c.line, c.start, c.end, c.raw) for c in find_phones(lines(doc))]
    assert found == [("+14155550142", "visible", 0, 4, 18, "(415) 555-0142"), ("+12125550169", "visible", 0, 24, 36, "212-555-0169"),
                     ("+19195550124", "white", 2, 0, 12, "919.555.0124")]
    text = lines(doc)[0].text
    assert text[4:18] == "(415) 555-0142" and text[24:36] == "212-555-0169"


def test_an_attachment_takes_its_emails_custody():
    email = page()
    att = page() | {"id": "d2", "custody": email["custody"] | {"channel": "vendor_portal",
                                                               "received_at": "2020-01-01T00:00:00Z",
                                                               "parent_document_id": "d1"}}
    parent = custody_for(email)
    c = custody_for(att, parent=parent)
    assert (c.channel, c.received_at) == ("email", dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    with pytest.raises(ValueError):
        custody_for(att)  # an attachment without its parent is refused, not guessed


def test_the_hash_is_the_contents_not_the_custody():
    a, b = page(span("x")), page(span("x"))
    b["custody"] = b["custody"] | {"sender": "someone@else.example"}
    assert document_sha256(a) == document_sha256(b) != document_sha256(page(span("y")))
    assert len(document_sha256(a)) == 64


@pytest.mark.parametrize("path", TRUTHS, ids=lambda p: p.stem)
def test_gate_a_every_planted_number_and_nothing_else(path):
    truth = json.loads(path.read_text())
    docs = json.loads((ROOT / "corpus" / truth["documents"]).read_text())
    want = Counter((s["document_id"], p["e164"], s["visibility"]) for p in truth["planted"] for s in p["sightings"])
    got = Counter((d["id"], c.e164, c.visibility) for d in docs for c in find_phones(lines(d)))
    assert got == want


def test_anything_unreadable_counts_as_hidden():
    doc = page(span("no colour", color=None), span("odd colour", color="blue"), span("no x", x=None),
               span("text x", x="left"), span("nan size", size=float("nan")), span("inf y", y=float("inf")))
    assert [ln.visibility for ln in lines(doc)] == ["white", "white", "off_page", "off_page", "off_page", "off_page"]
