"""Spec 0041: `choose_anchor`, the onboarding anchor rule (acceptance items 1 to 4)."""
from __future__ import annotations

import copy
import datetime as dt

import pytest
from dialback_onboarding import anchor
from dialback_onboarding.anchor import REASONS, Settings, choose_anchor
from hypothesis import given, seed
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

AS_OF = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)
SETTINGS = Settings(as_of=AS_OF)
REAL = "+14155550120"      # the vendor's number, as the registry and its website show it
OTHER = "+14155550121"     # a number only the onboarding entry supplies

GOOD = {
    "vendor": {"id": "v1", "name": "Acme Fasteners LLC", "state": "CA", "tax_id": "00-0000001"},
    "contacts": [{"phone_number": REAL}],
    "sources": {
        "registry": {"legal_name": "ACME FASTENERS, LLC", "state": "ca", "tax_id": "000000001",
                     "status": "Active", "filed_at": "2015-03-02", "phone": "(415) 555-0120"},
        "website": {"domain": "acme.example", "registered_at": "2014-06-01", "phone": "415.555.0120"},
    },
}


def run(case: dict, rule=choose_anchor, settings: Settings = SETTINGS) -> dict:
    return rule(case["vendor"], case["contacts"], case["sources"], settings)


def changed(*edits: tuple[tuple[str, ...], object]) -> dict:
    """GOOD with each (path, value) edit applied."""
    case = copy.deepcopy(GOOD)
    for path, value in edits:
        node = case
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return case


def _kinds_showing(case: dict, number: str) -> int:
    return sum(1 for kind in anchor.INDEPENDENT_KINDS
               if isinstance(case["sources"].get(kind), dict)
               and anchor.e164(case["sources"][kind].get("phone")) == number)


# 1. Agreeing sources anchor ------------------------------------------------------------------------

def test_agreeing_sources_anchor():
    r = run(GOOD)
    assert r["outcome"] == "anchored"
    assert r["anchor"] == REAL
    assert r["source_kinds"] == ["registry", "website"]
    assert r["reasons"] == [] and r["flags"] == []
    assert r["supplied_numbers"] == [REAL]


def test_an_anchor_the_vendor_did_not_supply_is_flagged():
    # Contacts never count, so having none (or an unreadable list) only means nothing was supplied.
    for contacts in ([{"phone_number": OTHER}], [], None):
        r = run(changed((("contacts",), contacts)))
        assert r["outcome"] == "anchored" and r["anchor"] == REAL
        assert r["flags"] == ["supplied_number_unconfirmed"]


def test_a_domain_exactly_old_enough_passes():
    exactly = (AS_OF - dt.timedelta(days=365)).isoformat()
    assert run(changed((("sources", "website", "registered_at"), exactly)))["outcome"] == "anchored"


# 3. Each reason fires on its own case and blocks the anchor -----------------------------------------

YOUNG = (AS_OF - dt.timedelta(days=364)).isoformat()
CASES = [   # (the reason the case is about, the edits to GOOD, the exact reasons expected)
    # With no entry, its status, name, state and tax ID are missing facts, and each fails its check.
    ("no_registry_entry", [(("sources", "registry"), None)],
     {"no_registry_entry", "registry_inactive", "registry_mismatch", "tax_id_mismatch",
      "too_few_independent_sources"}),
    ("registry_inactive", [(("sources", "registry", "status"), "Dissolved")], {"registry_inactive"}),
    ("registry_mismatch", [(("sources", "registry", "legal_name"), "Acme Fastener Supply LLC")],
     {"registry_mismatch"}),
    ("registry_mismatch", [(("sources", "registry", "state"), "NV")], {"registry_mismatch"}),
    ("registry_mismatch", [(("vendor", "state"), None)], {"registry_mismatch"}),
    ("tax_id_mismatch", [(("vendor", "tax_id"), "00-0000002")], {"tax_id_mismatch"}),
    ("tax_id_mismatch", [(("vendor", "tax_id"), None)], {"tax_id_mismatch"}),
    ("domain_too_young", [(("sources", "website", "registered_at"), YOUNG)], {"domain_too_young"}),
    ("domain_too_young", [(("sources", "website", "registered_at"), "last spring")], {"domain_too_young"}),
    ("domain_too_young", [(("sources", "website"), None)],
     {"domain_too_young", "too_few_independent_sources"}),
    ("too_few_independent_sources", [(("sources", "website", "phone"), None)],
     {"too_few_independent_sources"}),
    ("too_few_independent_sources", [(("sources", "website", "phone"), "call us")],
     {"too_few_independent_sources"}),
    ("conflicting_independent_numbers", [(("sources", "website", "phone"), "(415) 555-0121")],
     {"conflicting_independent_numbers", "too_few_independent_sources"}),
]


def test_every_reason_has_a_case():
    assert {reason for reason, _, _ in CASES} == set(REASONS)


@pytest.mark.parametrize(("reason", "edits", "expected"), CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_each_reason_blocks_the_anchor(reason, edits, expected):
    r = run(changed(*edits))
    assert reason in expected
    assert r["outcome"] == "no_anchor" and r["anchor"] is None and r["source_kinds"] == []
    assert set(r["reasons"]) == expected
    assert r["reasons"] == [x for x in REASONS if x in expected]     # a fixed order
    assert r["flags"] == []


# Each case differs from the anchored GOOD in one fact. Removing the check it is about (a deliberately
# broken rule) must make that case anchor, unless another check backs it up, which is named here.
FIRST_CASE = {reason: edits for reason, edits, _ in reversed(CASES)}
BACKSTOPPED = {
    "no_registry_entry": "with no registry entry, the registry checks and the number count fail too",
    "conflicting_independent_numbers": "with two kinds, a disagreement also leaves too few on any number",
}


@pytest.mark.parametrize("reason", REASONS)
def test_a_rule_missing_a_check_is_caught(reason, monkeypatch):
    monkeypatch.setattr(anchor, "CHECKS", tuple(c for c in anchor.CHECKS if c[0] != reason))
    r = run(changed(*FIRST_CASE[reason]))
    assert reason not in r["reasons"]
    if reason in BACKSTOPPED:
        assert r["outcome"] == "no_anchor" and r["reasons"]
    else:
        assert r["outcome"] == "anchored"


def test_conflict_check_is_decisive_once_a_third_kind_exists(monkeypatch):
    # Q71: a third independent kind (such as the buyer's own record) would make two agree and one
    # disagree; then the conflict check alone stands between the case and an anchor.
    monkeypatch.setattr(anchor, "INDEPENDENT_KINDS", ("registry", "website", "buyer_record"))
    case = changed((("sources", "buyer_record"), {"phone": OTHER}))
    assert run(case)["reasons"] == ["conflicting_independent_numbers"]
    monkeypatch.setattr(anchor, "CHECKS", tuple(c for c in anchor.CHECKS
                                                if c[0] != "conflicting_independent_numbers"))
    assert run(case)["outcome"] == "anchored"


# 2. A number only the vendor's own contacts show is never the anchor (a property) ------------------

def mostly(good, *bad):
    """Usually the value that passes, so that many worlds anchor and the property has teeth."""
    return st.sampled_from([good] * 20 + list(bad))


def usually(strategy):
    """The source exists in most worlds. (`st.one_of` does not weight repeated branches.)"""
    return mostly(True, False).flatmap(lambda present: strategy if present else st.none())


SOURCE_PHONES = mostly(REAL, "(415) 555-0120", "415.555.0120", OTHER, None, "call us", 4155550120)
REGISTRY = usually(st.fixed_dictionaries({
    "legal_name": mostly("Acme Fasteners LLC", "Acme Fastener Supply", None),
    "state": mostly("CA", "NV", None), "tax_id": mostly("00-0000001", "00-0000002", None),
    "status": mostly("active", "Dissolved", None), "phone": SOURCE_PHONES}))
WEBSITE = usually(st.fixed_dictionaries({
    "domain": st.just("acme.example"), "registered_at": mostly("2014-06-01", YOUNG, None, "last spring"),
    "phone": SOURCE_PHONES}))
CONTACT_PHONES = st.sampled_from([REAL, OTHER, "+14155550122", "(415) 555-0121", None, 4155550121])
WORLDS = st.fixed_dictionaries({
    "vendor": st.fixed_dictionaries({"id": st.just("v1"), "name": st.just("Acme Fasteners LLC"),
                                     "state": mostly("CA", None), "tax_id": mostly("00-0000001", None)}),
    "contacts": st.lists(st.fixed_dictionaries({"phone_number": CONTACT_PHONES}), max_size=3),
    "sources": st.fixed_dictionaries({"registry": REGISTRY, "website": WEBSITE}),
})


def _supplied_only_anchor(case: dict, rule=choose_anchor) -> bool:
    """Did the rule anchor on a number fewer than two independent kinds show?"""
    r = run(case, rule)
    return r["outcome"] == "anchored" and _kinds_showing(case, r["anchor"]) < 2


@seed(41)
@hyp_settings(max_examples=400, deadline=None)
@given(WORLDS)
def test_vendor_supplied_number_never_anchors(case):
    assert not _supplied_only_anchor(case)


def test_the_property_worlds_reach_anchors():
    # Floors set before the first measured run (the first cut anchored 0 of 400, so the property above
    # passed without testing anything): at least 20% of worlds anchor, and at least 5% anchor while
    # the onboarding entry supplies some other number.
    seen = {"n": 0, "anchored": 0, "other_supplied": 0}

    @seed(41)
    @hyp_settings(max_examples=400, deadline=None, database=None)
    @given(WORLDS)
    def probe(case):
        r = run(case)
        seen["n"] += 1
        if r["outcome"] == "anchored":
            seen["anchored"] += 1
            seen["other_supplied"] += any(n != r["anchor"] for n in r["supplied_numbers"])

    probe()
    assert seen["anchored"] >= 0.20 * seen["n"], seen
    assert seen["other_supplied"] >= 0.05 * seen["n"], seen


def test_the_property_catches_a_rule_that_counts_contacts():
    def counts_contacts(vendor, contacts, sources, settings):
        # Broken on purpose: a registry entry with no phone borrows the onboarding entry's number.
        sources = copy.deepcopy(sources)
        if isinstance(sources.get("registry"), dict) and sources["registry"].get("phone") is None and contacts:
            sources["registry"]["phone"] = contacts[0]["phone_number"]
        return choose_anchor(vendor, contacts, sources, settings)

    case = changed((("sources", "registry", "phone"), None))
    assert not _supplied_only_anchor(case)
    assert _supplied_only_anchor(case, counts_contacts)


# 4. Missing or bad data fails closed -----------------------------------------------------------------

BAD = [
    ("empty vendor", changed((("vendor",), {}))),
    ("no sources", changed((("sources",), {}))),
    ("sources of the wrong type", changed((("sources",), {"registry": "Acme", "website": 5}))),
    ("a number as an integer", changed((("sources", "website", "phone"), 4155550120))),
    ("a date as an integer", changed((("sources", "website", "registered_at"), 20140601))),
    ("a kind the rule does not count", changed((("sources", "website", "phone"), None),
                                                (("sources", "contacts"), {"phone": REAL}))),
    ("a number with an extension", changed((("sources", "website", "phone"), "(415) 555-0120 x12"))),
    ("a non-US number", changed((("sources", "website", "phone"), "+44 20 7946 0120"))),
]


@pytest.mark.parametrize(("why", "case"), BAD, ids=[b[0] for b in BAD])
def test_fails_closed_on_missing_or_bad_data(why, case):
    r = run(case)
    assert r["outcome"] == "no_anchor" and r["anchor"] is None and r["reasons"]


JSON = st.recursive(st.none() | st.booleans() | st.integers() | st.text(max_size=12),
                    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=8), inner, max_size=3),
                    max_leaves=10)


@seed(41)
@hyp_settings(max_examples=300, deadline=None)
@given(JSON, JSON, JSON)
def test_never_raises_on_any_json(vendor, contacts, sources):
    r = choose_anchor(vendor, contacts, sources, SETTINGS)
    assert r["outcome"] in ("anchored", "no_anchor")
