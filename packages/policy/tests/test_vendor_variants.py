"""The first word a vendor's name may be said by on its own (spec 0082, D2), and the guard's name
variants, which must not change when that test moves into its own helper (A6)."""
from __future__ import annotations

import re

import pytest
from dialback_policy import guard
from dialback_policy.guard import vendor_variants
from hypothesis import given
from hypothesis import strategies as st


def _vendor_variants_before_0082(names: tuple[str, ...]) -> set[str]:
    """`vendor_variants` exactly as it was before spec 0082, kept here as the reference."""
    variants = set()
    for name in names:
        tokens = [t for t in re.findall(r"[a-z0-9]+", guard.clean(name)) if t not in guard._COMPANY_SUFFIXES]
        if not tokens:
            continue
        full = " ".join(tokens)
        if len(full) >= 2:
            variants.add(full)
        if len(tokens) > 1:
            variants.add("".join(tokens))
        if len(tokens) > 2:
            variants.add(" ".join(tokens[:2]))
        if len(tokens) > 1 and len(tokens[0]) >= 4 and tokens[0] not in guard._COMMON_FIRST_WORDS:
            variants.add(tokens[0])
    return variants


NAMES = [
    "Acme Fasteners LLC", "The Acme Group", "First Solar Inc", "Yes Energy LLC", "Acme Inc", "Acme",
    "Acme, Inc.", "ACME FASTENERS", "acme-fasteners llc", "Quick Ship Logistics", "First Choice Plumbing LLC",
    "Big Sky Lumber", "HP Inc", "3M Company", "A & B Supply", "Main Street Supply LLC", "General Electric Company",
    "Thin Air LLC", "Yelp Inc", "Dell Inc", "Brightline Office Supply Inc", "Pellam Industrial Supply",
    "Lindqvist Print Works", "O'Neil \"Quick\" Supply", "LLC", "The", "", "   ", "Ａｃｍｅ Ｆａｓｔｅｎｅｒｓ",
    "Acme\u200bFasteners", "Acme\x0bFasteners", "Café Bistro LLC", "Müller Tools GmbH", "84 Lumber", "Co Op Co",
    "Inc Acme Supply", "Acme™ Tools",
]


@pytest.mark.parametrize("name", NAMES)
def test_vendor_variants_are_unchanged_for_each_name(name: str) -> None:
    assert vendor_variants((name,)) == _vendor_variants_before_0082((name,))


def test_vendor_variants_are_unchanged_for_all_names_together() -> None:
    assert vendor_variants(tuple(NAMES)) == _vendor_variants_before_0082(tuple(NAMES))


@given(st.lists(st.text(max_size=40), max_size=4))
def test_vendor_variants_are_unchanged_for_any_names(names: list[str]) -> None:
    assert vendor_variants(tuple(names)) == _vendor_variants_before_0082(tuple(names))


@pytest.mark.parametrize(("name", "first"), [
    ("Acme Fasteners LLC", "acme"),
    ("The Acme Group", "acme"),          # "the" is dropped like a legal suffix
    ("ACME Fasteners", "acme"),
    ("Brightline Office Supply Inc", "brightline"),
    ("First Solar Inc", None),           # "first" is a common first word
    ("Main Street Supply LLC", None),
    ("Yes Energy LLC", None),            # "yes" is under 4 characters
    ("Acme Inc", None),                  # one word once the suffix is gone
    ("Acme", None),
    ("LLC", None),
    ("", None),
])
def test_distinctive_first_word(name: str, first: str | None) -> None:
    assert guard.distinctive_first_word(name) == first


@pytest.mark.parametrize("name", NAMES)
def test_vendor_variants_hold_the_distinctive_first_word(name: str) -> None:
    first = guard.distinctive_first_word(name)
    if first is not None:
        assert first in vendor_variants((name,))
