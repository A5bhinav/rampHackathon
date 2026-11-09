"""Spec 0041: the onboarding anchor rule. Pure: no I/O and no clock (the caller passes `as_of`).

A new vendor's **anchor** is one number shown by at least two independent sources, sources the vendor's
own onboarding entry did not supply, for a business whose registry entry and website pass every check.
The vendor's own Ramp contacts are the onboarding request: the result shows them and never counts
them. Anything missing or unreadable fails its check, so the result is `no_anchor`, never an anchor.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

RULE_VERSION = "onboarding-anchor-v0"
REASONS = (
    "no_registry_entry",
    "registry_inactive",
    "registry_mismatch",
    "tax_id_mismatch",
    "domain_too_young",
    "too_few_independent_sources",
    "conflicting_independent_numbers",
)
INDEPENDENT_KINDS = ("registry", "website")   # the vendor's own contacts are never one of these
UNCONFIRMED = "supplied_number_unconfirmed"


@dataclasses.dataclass(frozen=True)
class Settings:
    as_of: dt.datetime
    min_domain_age_days: int = 365
    min_independent_kinds: int = 2

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of needs a time zone")


def e164(raw: object) -> str | None:
    """A US number as +1 and ten digits, or None for anything else: not a string, an extension, a
    number from another country, or a number no US line can have."""
    if not isinstance(raw, str):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        return None
    return "+1" + digits


def _text(raw: object) -> str | None:
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def normalize_name(raw: object) -> str | None:
    """Case, punctuation and spacing don't count; every word does ("ACME FASTENERS, LLC" is
    "Acme Fasteners LLC", but "Acme Fasteners" is not)."""
    text = _text(raw)
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold()).split()) or None if text else None


def _state(raw: object) -> str | None:
    text = _text(raw)
    return text.upper() if text else None


def _tax_id(raw: object) -> str | None:
    digits = re.sub(r"\D", "", raw) if isinstance(raw, str) else ""
    return digits if len(digits) == 9 else None


def _when(raw: object) -> dt.datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        when = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


@dataclasses.dataclass(frozen=True)
class Facts:
    vendor: Mapping[str, Any]
    registry: Mapping[str, Any] | None
    website: Mapping[str, Any] | None
    phones: dict[str, str]        # independent kind -> the number it shows
    settings: Settings


def _registered(f: Facts) -> bool:
    return f.registry is not None


def _active(f: Facts) -> bool:
    status = _text(f.registry.get("status")) if f.registry else None
    return status is not None and status.casefold() == "active"


def _matches(f: Facts) -> bool:
    if f.registry is None:
        return False
    name, state = normalize_name(f.vendor.get("name")), _state(f.vendor.get("state"))
    return (name is not None and name == normalize_name(f.registry.get("legal_name"))
            and state is not None and state == _state(f.registry.get("state")))


def _tax_id_matches(f: Facts) -> bool:
    tax_id = _tax_id(f.vendor.get("tax_id"))
    return f.registry is not None and tax_id is not None and tax_id == _tax_id(f.registry.get("tax_id"))


def _domain_old_enough(f: Facts) -> bool:
    registered = _when(f.website.get("registered_at")) if f.website else None
    return registered is not None and \
        f.settings.as_of - registered >= dt.timedelta(days=f.settings.min_domain_age_days)


def _enough_kinds(f: Facts) -> bool:
    counts = Counter(f.phones.values())
    return bool(counts) and max(counts.values()) >= f.settings.min_independent_kinds


def _kinds_agree(f: Facts) -> bool:
    return len(set(f.phones.values())) <= 1


# Each check passes or names its reason. The anchor is set only when every check passes.
CHECKS: tuple[tuple[str, Callable[[Facts], bool]], ...] = (
    ("no_registry_entry", _registered),
    ("registry_inactive", _active),
    ("registry_mismatch", _matches),
    ("tax_id_mismatch", _tax_id_matches),
    ("domain_too_young", _domain_old_enough),
    ("too_few_independent_sources", _enough_kinds),
    ("conflicting_independent_numbers", _kinds_agree),
)


def _mapping(raw: object) -> Mapping[str, Any] | None:
    return raw if isinstance(raw, Mapping) else None


def _most_shown(phones: dict[str, str]) -> str | None:
    """The number the most independent kinds show, or None on a tie or when none shows one."""
    ranked = Counter(phones.values()).most_common()
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def choose_anchor(vendor: object, contacts: object, sources: object, settings: Settings) -> dict[str, Any]:
    """Decide one new vendor's anchor from its Ramp record, its Ramp contacts and its sources."""
    vendor_map = _mapping(vendor) or {}
    source_map = _mapping(sources) or {}
    phones = {}
    for kind in INDEPENDENT_KINDS:
        number = e164(source.get("phone")) if (source := _mapping(source_map.get(kind))) else None
        if number:
            phones[kind] = number
    facts = Facts(vendor_map, _mapping(source_map.get("registry")), _mapping(source_map.get("website")),
                  phones, settings)

    failed = {reason for reason, check in CHECKS if not check(facts)}
    best = _most_shown(phones)
    anchor = best if not failed and best is not None else None
    supplied = sorted({n for c in (contacts if isinstance(contacts, list | tuple) else ())
                       if (m := _mapping(c)) and (n := e164(m.get("phone_number")))})
    return {
        "vendor_id": _text(vendor_map.get("id")),
        "rule_version": RULE_VERSION,
        "outcome": "anchored" if anchor else "no_anchor",
        "anchor": anchor,
        "source_kinds": [k for k in INDEPENDENT_KINDS if anchor and phones.get(k) == anchor],
        "independent_numbers": {k: phones.get(k) for k in INDEPENDENT_KINDS},
        "reasons": [r for r in REASONS if r in failed],
        "flags": [UNCONFIRMED] if anchor and anchor not in supplied else [],
        "supplied_numbers": supplied,
    }
