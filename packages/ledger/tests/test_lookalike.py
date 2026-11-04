"""Spec 0015: lookalike domains are flagged, never merged."""
import json

from _world import ROOT
from dialback_ledger.lookalike import is_lookalike


def test_every_corpus_lookalike_is_flagged():
    for path in (ROOT / "corpus" / "truth").glob("*.json"):
        v = json.loads(path.read_text())["vendor"]
        assert is_lookalike(v["lookalike"], [v["domain"]]), v


def test_the_vendors_own_and_unrelated_domains_are_not():
    assert not is_lookalike("denholm.example", ["denholm.example"])
    assert not is_lookalike("mailbox.example", ["denholm.example"])
    assert not is_lookalike("brightwell.example", ["corvane.example"])


def test_homoglyphs_and_small_edits():
    assert is_lookalike("acrne.example", ["acme.example"])        # rn for m
    assert is_lookalike("brightwe1l.example", ["brightwell.example"])
    assert is_lookalike("brightwel.example", ["brightwell.example"])
    assert is_lookalike("BrightWell.Example", ["brightwell.example"]) is False  # same domain, other case
