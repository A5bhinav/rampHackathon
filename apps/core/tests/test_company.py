"""Did the person name this vendor (spec 0082, Part B): the caller's whole turn, as said, against
the vendor's legal and trade names. Unit tests, no database."""
from __future__ import annotations

import re

import pytest
from dialback_core import company
from dialback_core.company import company_matches, every_name_matches
from dialback_policy.guard import clean, vendor_variants

ACME = ("Acme Fasteners LLC",)
BRIGHTLINE = ("Brightline Office Supply Inc",)


def matches(said: str | None, names: tuple[str, ...]) -> bool | None:
    return company_matches(said, names[0], names[1:])


def said_words(text: str) -> list[str]:
    """The answer's words as the speech guard reads them."""
    return re.findall(r"[a-z0-9]+", clean(text))


# B1: what was said, the vendor's names, and the variant of the vendor's name that was said (B6).
SHORT_NAMES = [
    ("Acme", ACME, "acme"),
    ("This is Acme", ACME, "acme"),
    ("Hi, Acme accounts payable", ACME, "acme"),
    ("AcmeFasteners", ACME, "acmefasteners"),
    ("Brightline Office", BRIGHTLINE, "brightline office"),
]
TODAYS_MATCHES = [
    ("Acme Fasteners", ACME, "acme fasteners"),
    ("Fasteners, Acme", ACME, "acme"),
]
B1 = SHORT_NAMES + TODAYS_MATCHES


@pytest.mark.parametrize("said, names, variant", B1, ids=[s for s, _, _ in B1])
def test_b1_short_names_match_and_todays_matches_still_do(said, names, variant):
    assert matches(said, names) is True


# B2: clearly different companies, including near-sounding and common-word cases (the builder check).
B2 = [
    ("Globex Corporation", "Acme Fasteners LLC"),
    ("Hi, this is Globex, how can I help you?", "Yelp Inc"),
    ("This is Globex", "Thin Air LLC"),
    ("You've reached the main line at Globex", "Main Street Supply LLC"),
    ("General Dynamics", "General Electric Company"),
    ("Bell Canada", "Dell Inc"),
    ("We're a plumbing firm, Globex", "A & B Supply"),
]


@pytest.mark.parametrize("said, vendor", B2, ids=[f"{s} / {v}" for s, v in B2])
def test_b2_clearly_different_companies_stay_false(said, vendor):
    assert company_matches(said, vendor) is False


@pytest.mark.parametrize("said", ["acne", "This is acne"])
def test_b3_no_fuzzy_matching_so_a_misheard_name_stays_false(said):
    assert company_matches(said, "Acme Fasteners LLC") is False


@pytest.mark.parametrize("said, vendor", [("First", "First Solar Inc"), ("Main", "Main Street Supply LLC")])
def test_b4_a_common_first_word_alone_does_not_match(said, vendor):
    assert said.lower() not in vendor_variants((vendor,)), "the guard's common-word list keeps it out"
    assert company_matches(said, vendor) is False


def test_b5_llc_alone_and_no_answers_are_unknown():
    assert company_matches("LLC", "Acme Fasteners LLC") is None
    assert every_name_matches(["LLC"], "Acme Fasteners LLC") is None
    assert every_name_matches([], "Acme Fasteners LLC") is None


def test_b5_several_answers_any_false_is_false_then_any_unknown_is_unknown():
    vendor = "Acme Fasteners LLC"
    assert every_name_matches(["This is Acme", "Globex Corporation"], vendor) is False
    assert every_name_matches(["Globex Corporation", "LLC"], vendor) is False
    assert every_name_matches(["This is Acme", "LLC"], vendor) is None
    assert every_name_matches(["This is Acme", "Acme Fasteners"], vendor) is True


@pytest.mark.parametrize("said, legal_name, trade_names, today", [
    ("Acme", "", (), False),
    ("Acme", "LLC", (), False),
    ("This is Acme", "The Company", ("Inc",), False),
    ("LLC", "", (), None),
    ("", "", (), None),
    (None, "LLC", (), None),
], ids=["empty", "suffix-only", "suffixes-only-with-trade-name", "llc-to-empty", "nothing-to-empty", "none"])
def test_b5_a_vendor_with_no_usable_name_gives_todays_result(said, legal_name, trade_names, today):
    assert vendor_variants((legal_name, *trade_names)) == set()
    assert company_matches(said, legal_name, trade_names) is today


@pytest.mark.parametrize("said, names, variant", B1, ids=[s for s, _, _ in B1])
def test_b6_the_variant_that_matches_is_one_of_the_guards(said, names, variant, monkeypatch):
    assert variant in vendor_variants(names)
    words, run = said_words(said), variant.split()
    assert any(words[i:i + len(run)] == run for i in range(len(words) - len(run) + 1))
    monkeypatch.setattr(company, "vendor_variants", lambda _names: {variant})
    assert matches(said, names) is True, "that one variant is enough"


@pytest.mark.parametrize("said, names, variant", SHORT_NAMES, ids=[s for s, _, _ in SHORT_NAMES])
def test_b6_short_names_match_only_through_the_guards_variants(said, names, variant, monkeypatch):
    monkeypatch.setattr(company, "vendor_variants", lambda _names: set())
    assert matches(said, names) is False


NAMES = ["", "LLC", "The Company", "&", "Ünïcödé Werkzeug GmbH", "株式会社テスト", "Ａｃｍｅ Ｆａｓｔｅｎｅｒｓ",
         "Ac\u200bme Fasteners", "Acme\x0bFasteners LLC"]
ANSWERS = [None, "", " ", "LLC", "Acme", "株式会社", "\u200b", "Ａｃｍｅ", "This is Acme", "Ünïcödé"]


@pytest.mark.parametrize("name", NAMES, ids=[repr(n) for n in NAMES])
def test_b7_the_matcher_is_pure_and_total(name):
    for said in ANSWERS:
        first = company_matches(said, name, (name,))
        assert first in (True, False, None)
        assert company_matches(said, name, (name,)) is first
    assert every_name_matches([a for a in ANSWERS if a is not None], name, [name]) in (True, False, None)


# D6, beyond B1 to B7: a variant matches only as a run of whole words in a row, and trade names
# count. None of B1 to B7 would notice the guard's loose run-together check ("thinair" inside
# "withinairsystems"), a variant's words said apart, or trade names left out.
@pytest.mark.parametrize("said, vendor", [
    ("Within Air Systems", "Thin Air LLC"),
    ("Domain Street Supply", "Main Street Supply LLC"),
    ("The main line, on Elm Street", "Main Street Supply LLC"),
], ids=["run-together-inside-within-air", "run-together-inside-domain-street", "words-apart"])
def test_d6_a_variant_matches_only_as_a_run_of_whole_words(said, vendor):
    assert company_matches(said, vendor) is False


def test_d6_trade_names_give_variants_too():
    assert company_matches("This is Acme", "Globex Holdings Inc", ("Acme Fasteners",)) is True
