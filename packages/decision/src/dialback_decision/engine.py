"""decide(facts) -> Decision. Blocks are evaluated in order and the first one that matches
decides: Not verified, then Needs review, then Verified (every condition required), and
anything else is Needs review, including any error (R3, R7).

This is the skeleton engine (`ENGINE_VERSION` ends in `-skeleton`). It applies every rule
it can evaluate from the facts available today. Three facts need data no slice supplies yet
(the ledger's change context, per-turn speaker identity); the engine records them in
`rules_fired` as `U..` "not checked by this engine version" entries instead of guessing, so
the evidence shows exactly what was not checked. The full rule table (4 + 16 + 12 rules) is
the Nov 4 deliverable; it fails closed on those facts.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import rfc8785

from .facts import CaseFacts

ENGINE_VERSION = "decision-0.1.1-skeleton"  # 0.1.1: the spec 0005 facts; earlier calls count against (NV01-NV03)


@dataclass(frozen=True)
class Rule:
    id: str
    template: str                       # the sentence the dashboard shows; never "safe"
    test: Callable[[CaseFacts], bool]


def _checks(f: CaseFacts, predicate: Callable) -> bool:
    return f.checks is not None and predicate(f.checks)


def _earlier(f: CaseFacts, predicate: Callable) -> bool:
    """An earlier call of this generation counts against the change, never for it (spec 0005, D2)."""
    return any(predicate(call) for call in f.earlier_calls)


def _one(values: tuple[str, ...]) -> str | None:
    """The skeleton's reading of a repeated answer: its value if every recording agrees, else
    unknown. The Nov 4 engine reads every value instead (spec 0006)."""
    return values[0] if values and all(v == values[0] for v in values) else None


NOT_VERIFIED: tuple[Rule, ...] = (
    Rule("NV01_vendor_denied_change", "The person said the vendor did not ask to change its bank details.",
         lambda f: _one(f.change_requested) == "no" or _earlier(f, lambda c: "no" in c.change_requested)),
    Rule("NV02_digits_mismatch", "The digits the person gave do not match the new account.",
         lambda f: f.last_four_result == "mismatch" or _earlier(f, lambda c: c.last_four_result == "mismatch")),
    Rule("NV03_digits_match_old_account", "The digits the person gave match the old account, not the new one.",
         lambda f: f.last_four_result == "matches_old_account"
         or _earlier(f, lambda c: c.last_four_result == "matches_old_account")),
    Rule("NV04_offered_number_from_request", "The person offered a phone number that first appeared in the change request.",
         lambda f: f.offered_number_from_request is True),
)

NEEDS_REVIEW: tuple[Rule, ...] = (
    Rule("NR01_no_live_person", "No person confirmed anything on this call.", lambda f: f.call_result != "answers_recorded"),
    Rule("NR02_no_recording_consent", "Recording consent was not given.", lambda f: _one(f.recording_consent) == "no"),
    Rule("NR03_wrong_company", "The company the person named does not match this vendor.", lambda f: f.company_matches is False),
    Rule("NR04_speaker_from_change_thread", "The person first appeared in the change request thread.",
         lambda f: f.speaker_in_change_thread is True),
    Rule("NR05_digits_near_miss", "The digits the person gave are one digit off the new account.",
         lambda f: f.last_four_result == "near_miss"),
    Rule("NR06_digits_not_provided", "The person did not give four digits.", lambda f: f.last_four_result == "not_provided"),
    Rule("NR07_digits_ambiguous", "The old and new accounts end in the same four digits, so the digits prove nothing.",
         lambda f: f.last_four_result == "ambiguous"),
    Rule("NR08_digits_inconsistent", "The person gave two different sets of digits.", lambda f: f.last_four_result == "inconsistent"),
    Rule("NR09_change_unsure", "The person was not sure the vendor asked for the change.", lambda f: _one(f.change_requested) == "unsure"),
    Rule("NR10_pressure", "The person pressed for approval.", lambda f: "pressure_to_approve" in f.flags),
    Rule("NR11_opt_out", "The person asked not to be called again.", lambda f: "opt_out" in f.flags),
    Rule("NR12_asked_what_is_checked", "The person asked what is being checked.", lambda f: "asked_what_is_checked" in f.flags),
    Rule("NR13_another_number", "The person asked us to call a different number.",
         lambda f: f.offered_number or "asked_to_call_another_number" in f.flags),
    Rule("NR14_answer_not_in_transcript", "An answer is not supported by the transcript turn it cites.",
         lambda f: _checks(f, lambda c: not c.answers_grounded)),
    Rule("NR15_script_violation", "The call broke the script (disclosure, spoken digits, a new number, or a tool violation).",
         lambda f: _checks(f, lambda c: not c.script_clean)),
    Rule("NR16_other_flag", "The call was flagged for a person to look at.", lambda f: "other" in f.flags),
    Rule("NR17_split_speakers", "One person confirmed the change and a different person gave the digits.",
         lambda f: f.same_person_yes_and_digits is False),
)

VERIFIED: tuple[Rule, ...] = (
    Rule("V01_eligible_number_dialed", "We dialed a number the Trust Ledger could prove, and we placed the call.",
         lambda f: f.dialed_eligible_number),
    Rule("V02_live_person", "A person answered and answered the questions.", lambda f: f.call_result == "answers_recorded"),
    Rule("V03_recording_consent", "The person agreed to the recorded call.", lambda f: _one(f.recording_consent) == "yes"),
    Rule("V04_company_matches", "The person named this vendor.", lambda f: f.company_matches is True),
    Rule("V05_speaker_named", "The person gave their name.", lambda f: f.speaker_named),
    Rule("V06_change_confirmed", "The person confirmed the vendor asked to change its bank details.",
         lambda f: _one(f.change_requested) == "yes"),
    Rule("V07_digits_match", "The person gave the last four digits of the new account.", lambda f: f.last_four_result == "match"),
    Rule("V08_answers_grounded", "Every answer appears in the transcript turn it cites.",
         lambda f: _checks(f, lambda c: c.answers_grounded)),
    Rule("V09_script_clean", "The disclosure came first and the script was followed.", lambda f: _checks(f, lambda c: c.script_clean)),
    Rule("V10_no_flags", "Nothing on the call was flagged.", lambda f: not f.flags and not f.offered_number),
)

# Facts the skeleton cannot evaluate yet: (fact, rule id, sentence).
UNCHECKED: tuple[tuple[str, str, str], ...] = (
    ("speaker_in_change_thread", "U01_speaker_thread_not_checked",
     "Not checked by this engine version: whether the person first appeared in the change request thread."),
    ("same_person_yes_and_digits", "U02_same_person_not_checked",
     "Not checked by this engine version: whether one person gave both the confirmation and the digits."),
    ("offered_number_from_request", "U03_offered_number_source_not_checked",
     "Not checked by this engine version: whether an offered number came from the change request."),
)

ALL_RULES = {r.id: r for r in (*NOT_VERIFIED, *NEEDS_REVIEW, *VERIFIED)}
TEMPLATES = {**{r.id: r.template for r in ALL_RULES.values()}, **{rid: text for _, rid, text in UNCHECKED},
             "NR99_nothing_decided": "No rule could verify this change, so a person must review it.",
             "NR98_engine_error": "The decision engine hit an error, so a person must review this change."}


@dataclass(frozen=True)
class Decision:
    outcome: str                      # verified | not_verified | needs_review
    rules_fired: tuple[str, ...]
    engine_version: str
    inputs_hash: str
    last_four_result: str | None

    @property
    def explanation(self) -> list[str]:
        return [TEMPLATES[r] for r in self.rules_fired if r in TEMPLATES]

    def body(self) -> dict:
        """The decision_made event body (evidence-event.schema.json, DecisionMadeBody)."""
        return {"outcome": self.outcome, "rules_fired": list(self.rules_fired), "engine_version": self.engine_version,
                "inputs_hash": self.inputs_hash, "last_four_result": self.last_four_result, "strict_mode": False,
                "source": "call"}


def inputs_hash(facts: CaseFacts) -> str:
    return hashlib.sha256(rfc8785.dumps(facts.to_json())).hexdigest()


def _decide(f: CaseFacts) -> tuple[str, tuple[str, ...]]:
    fired = tuple(r.id for r in NOT_VERIFIED if r.test(f))
    if fired:
        return "not_verified", fired
    fired = tuple(r.id for r in NEEDS_REVIEW if r.test(f))
    if fired:
        return "needs_review", fired
    if all(r.test(f) for r in VERIFIED):
        unchecked = tuple(rid for fact, rid, _ in UNCHECKED if getattr(f, fact) is None)
        return "verified", tuple(r.id for r in VERIFIED) + unchecked
    return "needs_review", ("NR99_nothing_decided",)


def decide(facts: CaseFacts) -> Decision:
    """Pure and total: any error becomes Needs review (R7)."""
    try:
        digest = inputs_hash(facts)
    except Exception:  # noqa: BLE001  (unhashable facts must still produce a decision)
        digest = "0" * 64
    try:
        outcome, fired = _decide(facts)
    except Exception:  # noqa: BLE001  (fail closed on any engine error)
        outcome, fired = "needs_review", ("NR98_engine_error",)
    return Decision(outcome, fired, ENGINE_VERSION, digest, facts.last_four_result)
