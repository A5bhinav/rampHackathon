"""Scenario format and loader for the voice-slice attack lab (spec 0017, Task 1).

A scenario states facts, not answers: how the caller behaves on the line, and what the Trust
Ledger would know. The oracle derives the required outcome from these, and the expected outcome
is frozen with a content hash before a run so results cannot move the goalposts (CLAUDE.md)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml
from dialback_policy.script import Phase

from .declared import UNCOMPUTED, uncomputed

# The nine scenario classes (evals/README.md). The list lives here, not in a schema, so a new
# class needs no contract change.
CLASSES = frozenset({
    "hijacked_thread_new_number", "lookalike_domain", "insider_changer", "planted_listing",
    "forged_documents", "inbound_impersonation", "line_takeover", "legit_bank_switch", "legit_odd_contact",
})
LAST4_MODES = frozenset({"keypad", "speech", "refuse", "dont_know"})
_CONSENT = frozenset({"yes", "no"})
_CHANGE = frozenset({"yes", "no", "unsure"})
_ANSWERED_BY = frozenset({"human", "machine"})
_DIRECTIONS = frozenset({"outbound", "inbound"})
_ENDS = frozenset({"answers_recorded", "voicemail", "failed", "no_answer"})
LEGIT_CLASSES = frozenset({"legit_bank_switch", "legit_odd_contact"})
_RULE_ID = re.compile(r"(V|NV|NR|S)[0-9]{2}_[a-z_]+")
_FOUR_DIGITS = re.compile(r"[0-9]{4}")


@dataclass(frozen=True)
class CallerBehavior:
    """How the text line answers, phase by phase. `inject` is an adversarial line played at
    `inject_phase` instead of the plain answer; `answered_by` sets who picked up (human | machine)."""
    consent: str
    company: str
    department: str
    name: str                       # answers the NAME question (spec 0034 asks name and role apart)
    role: str                       # answers the ROLE question
    change: str
    last4_mode: str
    last4: str = ""
    answered_by: str = "human"
    inject: str | None = None
    inject_phase: str | None = None
    offer_other_number: bool = False
    hang_up_at: str | None = None
    silent: bool = False
    # The author's judgment of what the caller said at the company question (spec 0048, A4): `vendor`
    # (names this vendor), `other` (another company, a negation, a reseller, a correction) or `partial`
    # (part of the name). The oracle reads this, never production's company check.
    company_is: str | None = None


@dataclass(frozen=True)
class DeclaredFacts:
    """The ledger's view a scenario stands in for (Q57). `None` means not known (fail closed, R7)."""
    dialed_eligible_number: bool
    new_last4: str                      # the new account's last four, as the case holds it
    dialed_number_is_pick: bool | None
    speaker_in_change_thread: bool | None
    offered_number_from_request: bool | None
    line_risk: bool | None = None       # telephony signal (forwarding, SIM swap): a line-takeover tell
    old_last4: str | None = None        # the old account's last four, when the case knows it
    direction: str = "outbound"         # outbound (we dialed) | inbound (they called us); production reads the call's


@dataclass(frozen=True)
class Scenario:
    id: str
    scenario_class: str
    is_attack: bool
    customer_name: str          # the Ramp customer whose AP team is calling
    vendor_name: str            # the real vendor being verified (company_matches compares to this)
    caller: CallerBehavior
    facts: DeclaredFacts
    expect_rule: str | None = None      # the decision rule that must fire (the defense under test)
    expect_end: str = "answers_recorded"  # how the call must end
    declared: tuple[str, ...] = ()      # facts production never computes this way (declared.UNCOMPUTED; A3)


def load_scenarios(directory: str | Path) -> list[Scenario]:
    """Every `*.yaml` in `directory` except names starting with `_` (templates), validated. Two
    scenarios under one id are an error (review fix F7)."""
    scenarios = [_parse(path) for path in sorted(Path(directory).glob("*.yaml")) if not path.name.startswith("_")]
    seen: set[str] = set()
    for s in scenarios:
        if s.id in seen:
            raise ValueError(f"two scenarios share the id '{s.id}'")
        seen.add(s.id)
    return scenarios


# The keys a scenario may use: anything else (a typo such as `injct`) is an error, not ignored (F7).
_KEYS = frozenset({"id", "class", "is_attack", "customer_name", "vendor_name", "caller", "facts", "expect_rule",
                   "expect_end", "declared"})
_CALLER_KEYS = frozenset({"consent", "company", "company_is", "department", "name", "role", "change", "last4_mode",
                          "last4", "answered_by", "inject", "inject_phase", "offer_other_number", "hang_up_at",
                          "silent"})
COMPANY_TRUTHS = frozenset({"vendor", "other", "partial"})
_FACT_KEYS = frozenset({"dialed_eligible_number", "new_last4", "old_last4", "dialed_number_is_pick",
                        "speaker_in_change_thread", "offered_number_from_request", "line_risk", "direction"})
_PHASES = frozenset(p.name for p in Phase)


def _parse(path: Path) -> Scenario:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: a scenario must be a mapping")  # noqa: TRY004  one error type for all bad files

    def need(key: str, where: dict[str, Any]) -> Any:
        if key not in where:
            raise ValueError(f"{path.name}: missing '{key}'")
        return where[key]

    def one_of(value: Any, allowed: frozenset[str], what: str) -> str:
        if value not in allowed:
            raise ValueError(f"{path.name}: {what} '{value}' is not one of {sorted(allowed)}")
        return value

    def only(where: Any, allowed: frozenset[str], what: str) -> dict[str, Any]:
        if not isinstance(where, dict):
            raise ValueError(f"{path.name}: {what} must be a mapping")  # noqa: TRY004  one error type for all bad files
        if extra := sorted(set(where) - allowed):
            raise ValueError(f"{path.name}: unknown {what} key(s) {extra}")
        return where

    def flag(value: Any, what: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{path.name}: {what} must be true or false, not {value!r}")  # noqa: TRY004
        return value

    def phase(value: Any, what: str) -> str | None:
        return None if value is None else one_of(value, _PHASES, what)

    only(raw, _KEYS, "scenario")
    c = only(need("caller", raw), _CALLER_KEYS, "caller")
    caller = CallerBehavior(
        consent=one_of(need("consent", c), _CONSENT, "consent"),
        company=str(need("company", c)),
        department=str(need("department", c)),
        name=str(need("name", c)),
        role=str(need("role", c)),
        change=one_of(need("change", c), _CHANGE, "change"),
        last4_mode=one_of(need("last4_mode", c), LAST4_MODES, "last4_mode"),
        last4=str(c.get("last4", "")),
        answered_by=one_of(c.get("answered_by", "human"), _ANSWERED_BY, "answered_by"),
        inject=c.get("inject"),
        inject_phase=phase(c.get("inject_phase"), "inject_phase"),
        offer_other_number=flag(c.get("offer_other_number", False), "offer_other_number"),
        hang_up_at=phase(c.get("hang_up_at"), "hang_up_at"),
        silent=flag(c.get("silent", False), "silent"),
        company_is=one_of(need("company_is", c), COMPANY_TRUTHS, "company_is"),
    )
    f = only(need("facts", raw), _FACT_KEYS, "facts")
    new_last4, old_last4 = str(need("new_last4", f)), f.get("old_last4")
    for what, digits in (("new_last4", new_last4), ("old_last4", old_last4)):
        if digits is not None and not _FOUR_DIGITS.fullmatch(str(digits)):
            raise ValueError(f"{path.name}: {what} must be four digits, not {digits!r}")
    facts = DeclaredFacts(
        dialed_eligible_number=flag(need("dialed_eligible_number", f), "dialed_eligible_number"),
        new_last4=new_last4,
        old_last4=None if old_last4 is None else str(old_last4),
        dialed_number_is_pick=_tri(path, f.get("dialed_number_is_pick")),
        speaker_in_change_thread=_tri(path, f.get("speaker_in_change_thread")),
        offered_number_from_request=_tri(path, f.get("offered_number_from_request")),
        line_risk=_tri(path, f.get("line_risk")),
        direction=one_of(f.get("direction", "outbound"), _DIRECTIONS, "direction"),
    )
    scenario_class = one_of(need("class", raw), CLASSES, "class")
    is_attack = flag(need("is_attack", raw), "is_attack")
    if is_attack == (scenario_class in LEGIT_CLASSES):
        raise ValueError(f"{path.name}: is_attack {is_attack} disagrees with class '{scenario_class}'")
    expect_rule = raw.get("expect_rule")
    if expect_rule is not None and not _RULE_ID.fullmatch(str(expect_rule)):
        raise ValueError(f"{path.name}: expect_rule '{expect_rule}' is not a decision rule id")
    declared = raw.get("declared") or []
    if not isinstance(declared, list) or not all(isinstance(d, str) for d in declared):
        raise ValueError(f"{path.name}: declared must be a list of fact names")
    if unknown := sorted(set(declared) - set(UNCOMPUTED)):
        raise ValueError(f"{path.name}: declared {unknown}: production computes these; only "
                         f"{sorted(UNCOMPUTED)} can be declared")
    sets = uncomputed(facts, caller)
    if missing := sorted(sets - set(declared)):
        raise ValueError(f"{path.name}: sets {missing} to a value production never computes; declare it "
                         "under `declared` (spec 0048, A3)")
    if extra := sorted(set(declared) - sets):
        raise ValueError(f"{path.name}: declared {extra} but does not set it to an uncomputed value")
    return Scenario(
        id=str(need("id", raw)),
        scenario_class=scenario_class,
        is_attack=is_attack,
        customer_name=str(need("customer_name", raw)),
        vendor_name=str(need("vendor_name", raw)),
        caller=caller,
        facts=facts,
        expect_rule=None if expect_rule is None else str(expect_rule),
        expect_end=one_of(raw.get("expect_end", "answers_recorded"), _ENDS, "expect_end"),
        declared=tuple(sorted(declared)),
    )


def _tri(path: Path, value: Any) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{path.name}: a tri-state fact must be true, false or absent, not {value!r}")
    return value


def expected_hash(s: Scenario) -> str:
    """A stable content hash of the scenario, frozen before a run (no goalpost moving)."""
    return hashlib.sha256(json.dumps(asdict(s), sort_keys=True).encode()).hexdigest()
