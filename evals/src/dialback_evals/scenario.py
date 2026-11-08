"""Scenario format and loader for the voice-slice attack lab (spec 0017, Task 1).

A scenario states facts, not answers: how the caller behaves on the line, and what the Trust
Ledger would know. The oracle derives the required outcome from these, and the expected outcome
is frozen with a content hash before a run so results cannot move the goalposts (CLAUDE.md)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

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
# The last-four comparison results decide() understands (dialback_decision.last_four).
_LAST_FOUR = frozenset({"match", "matches_old_account", "near_miss", "mismatch", "not_provided",
                        "ambiguous", "inconsistent"})


@dataclass(frozen=True)
class CallerBehavior:
    """How the text line answers, phase by phase. `inject` is an adversarial line played at
    `inject_phase` instead of the plain answer; `answered_by` sets who picked up (human | machine)."""
    consent: str
    company: str
    department: str
    name_role: str
    change: str
    last4_mode: str
    last4: str = ""
    answered_by: str = "human"
    inject: str | None = None
    inject_phase: str | None = None
    offer_other_number: bool = False
    hang_up_at: str | None = None
    silent: bool = False


@dataclass(frozen=True)
class DeclaredFacts:
    """The ledger's view a scenario stands in for (Q57). `None` means not known (fail closed, R7)."""
    dialed_eligible_number: bool
    last_four_result: str | None
    dialed_number_is_pick: bool | None
    speaker_in_change_thread: bool | None
    offered_number_from_request: bool | None


@dataclass(frozen=True)
class Scenario:
    id: str
    scenario_class: str
    is_attack: bool
    customer_name: str          # the Ramp customer whose AP team is calling
    vendor_name: str            # the real vendor being verified (company_matches compares to this)
    caller: CallerBehavior
    facts: DeclaredFacts


def load_scenarios(directory: str | Path) -> list[Scenario]:
    """Every `*.yaml` in `directory` except names starting with `_` (templates), validated."""
    return [_parse(path) for path in sorted(Path(directory).glob("*.yaml")) if not path.name.startswith("_")]


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

    c = need("caller", raw)
    caller = CallerBehavior(
        consent=one_of(need("consent", c), _CONSENT, "consent"),
        company=str(need("company", c)),
        department=str(need("department", c)),
        name_role=str(need("name_role", c)),
        change=one_of(need("change", c), _CHANGE, "change"),
        last4_mode=one_of(need("last4_mode", c), LAST4_MODES, "last4_mode"),
        last4=str(c.get("last4", "")),
        answered_by=one_of(c.get("answered_by", "human"), _ANSWERED_BY, "answered_by"),
        inject=c.get("inject"),
        inject_phase=c.get("inject_phase"),
        offer_other_number=bool(c.get("offer_other_number", False)),
        hang_up_at=c.get("hang_up_at"),
        silent=bool(c.get("silent", False)),
    )
    f = need("facts", raw)
    lfr = f.get("last_four_result")
    if lfr is not None:
        one_of(lfr, _LAST_FOUR, "last_four_result")
    facts = DeclaredFacts(
        dialed_eligible_number=bool(need("dialed_eligible_number", f)),
        last_four_result=lfr,
        dialed_number_is_pick=_tri(path, f.get("dialed_number_is_pick")),
        speaker_in_change_thread=_tri(path, f.get("speaker_in_change_thread")),
        offered_number_from_request=_tri(path, f.get("offered_number_from_request")),
    )
    return Scenario(
        id=str(need("id", raw)),
        scenario_class=one_of(need("class", raw), CLASSES, "class"),
        is_attack=bool(need("is_attack", raw)),
        customer_name=str(need("customer_name", raw)),
        vendor_name=str(need("vendor_name", raw)),
        caller=caller,
        facts=facts,
    )


def _tri(path: Path, value: Any) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{path.name}: a tri-state fact must be true, false or absent, not {value!r}")
    return value


def expected_hash(s: Scenario) -> str:
    """A stable content hash of the scenario, frozen before a run (no goalpost moving)."""
    return hashlib.sha256(json.dumps(asdict(s), sort_keys=True).encode()).hexdigest()
