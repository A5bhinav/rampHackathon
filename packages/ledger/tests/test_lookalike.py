"""Spec 0021: lookalike domains are flagged, never merged."""
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


def test_combosquats_other_suffixes_subdomains_and_punycode():
    for d in ("acme-billing.example", "acmepayments.example", "acme.co.uk", "acme.example.evil.com",
              "billing.acme-ap.com", "xn--acm-0na.example"):
        assert is_lookalike(d, ["acme.example"]), d
    assert not is_lookalike("mailbox.example", ["acme.example"])


def test_a_vendor_subdomain_is_the_vendor():
    assert not is_lookalike("ar.acme.example", ["acme.example"])


def test_a_sender_with_a_display_name_is_read_by_its_address():
    from dialback_ledger.lookalike import sender_domain
    assert sender_domain("Dana Whitfield <ar@Acme.Example>") == "acme.example"
    assert sender_domain('"Whitfield, Dana" <dana@acme.example>') == "acme.example"
    assert sender_domain("ar@acme.example") == "acme.example"
    assert sender_domain("no address") is None and sender_domain(None) is None
