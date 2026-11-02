"""decide(facts) -> Decision: pure and total (R3, R7, ADR-4). The rule table is `rules.py`
(spec 0006). Any error becomes Needs review; nothing a model says can set the outcome."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import rfc8785

from . import rules
from .facts import CaseFacts
from .last_four import LAST_FOUR_RESULTS

ENGINE_VERSION = "decision-1.0.0"

# Sentences for ids earlier engine versions recorded, so their decisions still render.
RETIRED = {
    "V01_eligible_number_dialed": "We dialed a number the Trust Ledger could prove, and we placed the call.",
    "V02_live_person": "A person answered and answered the questions.",
    "V03_recording_consent": "The person agreed to the recorded call.",
    "V04_company_matches": "The person named this vendor.",
    "V05_speaker_named": "The person gave their name.",
    "V06_change_confirmed": "The person confirmed the vendor asked to change its bank details.",
    "V07_digits_match": "The person gave the last four digits of the new account.",
    "V08_answers_grounded": "Every answer appears in the transcript turn it cites.",
    "V09_script_clean": "The disclosure came first and the script was followed.",
    "V10_no_flags": "Nothing on the call was flagged.",
    "U01_speaker_thread_not_checked": "Not checked by this engine version: whether the person first appeared in the change request thread.",
    "U02_same_person_not_checked": "Not checked by this engine version: whether one person gave both the confirmation and the digits.",
    "U03_offered_number_source_not_checked": "Not checked by this engine version: whether an offered number came from the change request.",
    "NR99_nothing_decided": "No rule could verify this change, so a person must review it.",
}


def _all_rules() -> tuple[rules.Rule, ...]:
    return (*rules.BLOCK0, *rules.NOT_VERIFIED, *rules.NEEDS_REVIEW, *rules.VERIFIED, *rules.STRICT)


TEMPLATES: dict[str, str] = {
    **RETIRED, **rules.FIXED,
    **{r.id: r.template for r in _all_rules()},
    **{r.unmet_id: r.unmet for r in (*rules.VERIFIED, *rules.STRICT) if r.unmet},
}


@dataclass(frozen=True)
class Decision:
    outcome: str                      # verified | not_verified | needs_review
    rules_fired: tuple[str, ...]
    engine_version: str
    inputs_hash: str
    last_four_result: str | None
    strict: bool = False
    source: str = "call"

    @property
    def explanation(self) -> list[str]:
        return [TEMPLATES[r] for r in self.rules_fired if r in TEMPLATES]

    def body(self) -> dict:
        """The decision_made event body (evidence-event.schema.json, DecisionMadeBody)."""
        body = {"outcome": self.outcome, "rules_fired": list(self.rules_fired), "engine_version": self.engine_version,
                "inputs_hash": self.inputs_hash, "strict_mode": self.strict is True,
                "source": self.source if self.source in rules.SOURCES else "call"}
        if self.last_four_result in LAST_FOUR_RESULTS:
            body["last_four_result"] = self.last_four_result
        return body


def inputs_hash(facts: CaseFacts, strict: bool = False, source: str = "call") -> str:
    """SHA-256 over the RFC 8785 form of everything decide() reads."""
    return hashlib.sha256(rfc8785.dumps({"facts": facts.to_json(), "strict": strict, "source": source})).hexdigest()


def _decide(f: CaseFacts, mode: rules.Mode) -> tuple[str, tuple[str, ...]]:
    if not rules.valid(f, mode):
        return "needs_review", (rules.INVALID,)
    for outcome, block in (("needs_review", rules.BLOCK0), ("not_verified", rules.NOT_VERIFIED),
                           ("needs_review", rules.NEEDS_REVIEW)):
        fired = tuple(r.id for r in block if r.test(f, mode))
        if fired:
            return outcome, fired
    conditions = (*rules.VERIFIED, *(rules.STRICT if mode.strict else ()))
    unmet = tuple(c.unmet_id for c in conditions if not c.test(f, mode))
    if unmet:
        return "needs_review", (rules.NOT_MET, *unmet)
    return "verified", tuple(c.id for c in conditions)


def decide(facts: CaseFacts, *, strict: bool = False, source: str = "call") -> Decision:
    """Pure and total: invalid facts are NR97 and any engine error is NR98 (R7)."""
    try:
        digest = inputs_hash(facts, strict, source)
    except Exception:  # noqa: BLE001  (unhashable input must still produce a decision)
        digest = "0" * 64
    try:
        outcome, fired = _decide(facts, rules.Mode(strict=strict, source=source))
    except Exception:  # noqa: BLE001  (fail closed on any engine error)
        outcome, fired = "needs_review", (rules.ENGINE_ERROR,)
    last_four = getattr(facts, "last_four_result", None)
    return Decision(outcome, fired, ENGINE_VERSION, digest, last_four if last_four in LAST_FOUR_RESULTS else None,
                    strict, source)
