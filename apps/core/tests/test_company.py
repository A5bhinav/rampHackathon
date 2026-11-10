"""Did the person name this vendor (spec 0082, Part B): the caller's whole turn, as said, against
the vendor's legal and trade names. Unit tests, no database.

D7b (Sami, Nov 10): only a refusal word (D7a) gives False. An answer that names someone else, or
nothing recognizable, is None, which blocks nothing; B2 to B6 and D6's former False rows are None."""
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
# None of them is a match; with no refusal word, D7b makes each unknown (B9).
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
def test_b2_clearly_different_companies_are_unknown(said, vendor):
    assert company_matches(said, vendor) is None


@pytest.mark.parametrize("said", ["acne", "This is acne"])
def test_b3_no_fuzzy_matching_so_a_misheard_name_is_unknown(said):
    assert company_matches(said, "Acme Fasteners LLC") is None


@pytest.mark.parametrize("said, vendor", [("First", "First Solar Inc"), ("Main", "Main Street Supply LLC")])
def test_b4_a_common_first_word_alone_does_not_match(said, vendor):
    assert said.lower() not in vendor_variants((vendor,)), "the guard's common-word list keeps it out"
    assert company_matches(said, vendor) is None


def test_b5_llc_alone_and_no_answers_are_unknown():
    assert company_matches("LLC", "Acme Fasteners LLC") is None
    assert every_name_matches(["LLC"], "Acme Fasteners LLC") is None
    assert every_name_matches([], "Acme Fasteners LLC") is None


def test_b5_several_answers_any_false_is_false_then_any_unknown_is_unknown():
    vendor = "Acme Fasteners LLC"
    assert every_name_matches(["This is Acme", "Not Acme, this is Globex"], vendor) is False
    assert every_name_matches(["Not Acme, this is Globex", "LLC"], vendor) is False
    assert every_name_matches(["This is Acme", "Globex Corporation"], vendor) is None, "D7b: not a refusal"
    assert every_name_matches(["This is Acme", "LLC"], vendor) is None
    assert every_name_matches(["This is Acme", "Acme Fasteners"], vendor) is True


@pytest.mark.parametrize("said, legal_name, trade_names, expected", [
    ("Acme", "", (), None),
    ("Acme", "LLC", (), None),
    ("This is Acme", "The Company", ("Inc",), None),
    ("LLC", "", (), None),
    ("", "", (), None),
    (None, "LLC", (), None),
    ("Not Acme", "", (), False),
], ids=["empty", "suffix-only", "suffixes-only-with-trade-name", "llc-to-empty", "nothing-to-empty", "none",
        "refusal-to-empty"])
def test_b5_a_vendor_with_no_usable_name_never_matches(said, legal_name, trade_names, expected):
    assert vendor_variants((legal_name, *trade_names)) == set()
    assert company_matches(said, legal_name, trade_names) is expected


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
    assert matches(said, names) is None


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
    assert company_matches(said, vendor) is None


def test_d6_trade_names_give_variants_too():
    assert company_matches("This is Acme", "Globex Holdings Inc", ("Acme Fasteners",)) is True


# B8 (spec 0082, D7a): refusal words override a match. Each of these names the vendor and says the
# person is not with it, or only works with it, so it must not count as naming the vendor.
REFUSALS = [
    "Not Acme, this is Globex",
    "This isn't Acme",
    "We're a reseller for Acme",
    "We distribute for Acme",
    "I used to work at Acme",
    "I was at Acme until last year",
    "Acme, sorry, I mean Globex",
]


@pytest.mark.parametrize("said", REFUSALS)
def test_b8_refusal_words_override_a_match(said):
    assert matches(said, ACME) is False


@pytest.mark.parametrize("said, names, expected", [
    ("No problem, this is Acme", ACME, True),          # bare "no" never counts
    ("Sorry, Acme Fasteners", ACME, True),             # bare "sorry" never counts
    ("Yes, Acme", ACME, True),
    ("Never Summer", ("Never Summer Industries",), True),  # a word in the vendor's own name doesn't count
    ("Acme? No, Globex", ACME, True),                  # accepted cost: a correction with no refusal word
    ("Not sure, I think this is Acme", ACME, False),   # the safe cost: an honest answer goes to review
], ids=["no-problem", "sorry", "yes", "own-name-word", "correction-cost", "not-sure-cost"])
def test_b8_honest_answers_and_pinned_costs(said, names, expected):
    assert matches(said, names) is expected


# B9 (spec 0082, D7b): code can't tell another company's name from a garbled vendor name, so an
# answer with no refusal word that doesn't match is unknown, and blocks nothing. The person who
# releases sees it on the call record. Only a refusal is False (NR03).
@pytest.mark.parametrize("said", ["This is Globex", "Globex Corporation", "Purple monkey dishwasher"],
                         ids=["this-is-globex", "globex-corporation", "nonsense"])
def test_b9_another_company_or_nonsense_is_unknown(said):
    assert matches(said, ACME) is None
    assert every_name_matches([said], ACME[0]) is None


def test_b9_a_refusal_in_any_answer_is_false():
    assert every_name_matches(["This is Globex", "Not Acme, this is Globex"], "Acme Fasteners LLC") is False
    assert every_name_matches(["Not Acme, this is Globex", "This is Globex"], "Acme Fasteners LLC") is False


# Review fixes (Nov 10): D7a's list exactly, each word and phrase on its own, and the ways a refusal can
# be written, read as the speech guard reads text.
D7A_WORDS = {"not", "isn", "aren", "wasn", "weren", "never", "former", "formerly", "until", "reseller",
             "resellers", "resell", "resells", "distribute", "distributes", "distributor", "behalf"}


def test_d7a_the_refusal_lists_are_exactly_the_specs():
    assert company.REFUSAL_WORDS == D7A_WORDS
    assert tuple(company.REFUSAL_PHRASES) == (("used", "to"), ("i", "mean"))


@pytest.mark.parametrize("said", [
    "Not Acme", "This isn't Acme", "They aren't Acme", "That wasn't Acme", "We weren't Acme", "Never Acme",
    "A former Acme office", "Formerly Acme", "Acme until last year", "An Acme reseller", "Acme resellers",
    "We resell Acme", "He resells Acme", "We distribute Acme", "She distributes Acme", "An Acme distributor",
    "On behalf of Acme", "I used to be Acme", "Acme, I mean Globex",
])
def test_d7a_each_refusal_word_and_phrase_flags_on_its_own(said):
    assert matches(said, ACME) is False


@pytest.mark.parametrize("said", ["NOT ACME", "This isn’t Acme",
                                  "Ｎｏｔ Ａｃｍｅ"],
                         ids=["uppercase", "curly-apostrophe", "fullwidth"])
def test_d7a_refusals_read_as_the_guard_reads_text(said):
    # The fullwidth "Not Acme" has no ASCII words, but the guard reads it as "not acme" (NFKC), and the
    # variant check already matches it that way, so the refusal check must read it the same way.
    assert matches(said, ACME) is False


@pytest.mark.parametrize("said, names", [
    ("I Mean It", ("I Mean It LLC",)),                          # a phrase wholly in the vendor's own name
    ("Never Summer", ("Globex Holdings Inc", "Never Summer")),  # a word in a trade name
], ids=["phrase-in-own-name", "trade-name-word"])
def test_d7a_the_vendors_own_words_are_not_refusals(said, names):
    assert matches(said, names) is True
