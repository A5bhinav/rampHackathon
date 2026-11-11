"""CaseFacts: everything decide() looks at (spec 0005).

Three sources, and the AI fills only the second:
1. Telephony, from the voice report: who answered, the direction, the number dialed.
2. Recorded answers: Sami's call-answers shape, never raw digits. A slot answered more than
   once keeps every value, in the order recorded; the engine decides what a contradiction means.
3. Computed by code after the call: the last-four result, the company match, the voice
   service's checks, the call order and the single speaker (from the answers' turns), earlier
   calls of this generation, and the ledger's change context.

`None` means "not known": a fact whose source hasn't supplied it, never a guess (R7).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CallChecks:
    disclosure_first: bool
    answers_grounded: bool
    agent_spoke_account_digits: bool
    new_number_accepted: bool
    policy_violations: int
    speech_guard_trips: int

    @property
    def script_clean(self) -> bool:
        return self.disclosure_first and not self.agent_spoke_account_digits and not self.new_number_accepted \
            and self.policy_violations == 0


@dataclass(frozen=True)
class EarlierCall:
    """What an earlier call of this generation said. It can count against a change, never for
    one: a redial's answers are never combined with a dropped call's (spec 0005, D2)."""
    result: str
    recording_consent: tuple[str, ...] = ()
    change_requested: tuple[str, ...] = ()
    last_four_result: str | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseFacts:
    call_result: str                                 # answers_recorded, no_answer, voicemail, ...
    dialed_eligible_number: bool                     # the report names this version's ledger pick
    # Recorded answers, every value in the order recorded.
    recording_consent: tuple[str, ...] = ()          # yes | no
    company_named: tuple[str, ...] = ()
    change_requested: tuple[str, ...] = ()           # yes | no | unsure
    speaker_named: bool = False
    speaker_role: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    offered_number: bool = False                     # the callee asked us to call another number
    # Computed by code.
    last_four_result: str | None = None
    digits_typed: bool = False                       # every account_last4 answer came from the keypad (spec 0080, B)
    company_matches: bool | None = None              # every company value against the vendor's names
    checks: CallChecks | None = None                 # computed by the voice service
    consent_first: bool | None = None                # D3
    answers_in_order: bool | None = None             # D3
    same_person_yes_and_digits: bool | None = None   # D4
    earlier_calls: tuple[EarlierCall, ...] = ()      # D2
    # Telephony from the voice report (D6). None until internal.yaml carries them.
    dialed_out: bool | None = None
    dialed_number_is_pick: bool | None = None
    answered_by: str | None = None                   # human | machine
    line_risk: bool | None = None                    # reserved: forwarding, SIM swap
    # The ledger's change context (D5). None until the ledger supplies it.
    speaker_in_change_thread: bool | None = None
    offered_number_from_request: bool | None = None
    speaker_known_before_request: bool | None = None  # strict mode
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["flags"] = sorted(self.flags)
        for call in data["earlier_calls"]:
            call["flags"] = sorted(call["flags"])
        return data


def values(answers: Iterable[dict[str, Any]], slot: str) -> tuple[Any, ...]:
    """Every value recorded for a slot, in the order recorded."""
    return tuple(a["value"] for a in answers if a["slot"] == slot)


def _turns(answers: list[dict[str, Any]], slot: str) -> list[int]:
    return [a["turn_id"] for a in answers if a["slot"] == slot]


def _name(value: str) -> str:
    """A spoken name, compared without case, spacing or punctuation."""
    return " ".join("".join(c for c in value.casefold() if c.isalnum() or c.isspace()).split())


def consent_first(answers: list[dict[str, Any]]) -> bool | None:
    """Consent is the first answer: no other answer shares or precedes its turn. A voicemail
    greeting can't supply a name before anyone agreed to the call."""
    if not answers:
        return None
    consent = _turns(answers, "recording_consent")
    if not consent:
        return False
    return all(a["turn_id"] > min(consent) for a in answers if a["slot"] != "recording_consent")


def answers_in_order(answers: list[dict[str, Any]]) -> bool | None:
    """The name is not after the change answer, the change answer is not after the digits, and
    the change answer cites a different turn than consent, so one "yes" can't answer both."""
    change = _turns(answers, "change_requested")
    if not change:
        return None
    name, digits = _turns(answers, "speaker_name"), _turns(answers, "account_last4")
    if name and min(name) > min(change):
        return False
    if digits and min(digits) < min(change):
        return False
    return not set(change) & set(_turns(answers, "recording_consent"))


def same_person(answers: list[dict[str, Any]]) -> bool | None:
    """One declared speaker: exactly one distinct name, given no later than the change answer.
    Two names (even "Jordan" and "Jordan Lee") are two people. This checks the declared identity
    stays the same; it doesn't prove who is speaking (spec 0006, limits)."""
    recorded = [(a["turn_id"], _name(a["value"])) for a in answers if a["slot"] == "speaker_name"]
    names = [(t, n) for t, n in recorded if n]
    if len({n for _, n in names}) > 1:
        return False
    if len(names) < len(recorded):
        return None  # a name we can't read could be anyone
    change = _turns(answers, "change_requested")
    if names and change and min(t for t, _ in names) <= min(change):
        return True
    return None


def digits_typed(answers: list[dict[str, Any]]) -> bool:
    """At least one digits answer, and every one typed on the keypad (spec 0080, Part B). A voicemail,
    fax or phone menu can't type them, so they show a live person. Every one, not any one, because the
    last-four result reads them all; a missing capture method counts as not typed (R7)."""
    digits = [a for a in answers if a["slot"] == "account_last4"]
    return bool(digits) and all(a.get("capture_method") == "keypad" for a in digits)


def earlier_call(body: dict[str, Any]) -> EarlierCall:
    """Summarize a stored call_ended body (evidence-event.schema.json, CallEndedBody)."""
    answers = (body.get("answers") or {}).get("answers") or []
    return EarlierCall(result=body["result"], recording_consent=values(answers, "recording_consent"),
                       change_requested=values(answers, "change_requested"),
                       last_four_result=body.get("last_four_result"), flags=values(answers, "flag"))


def facts_from_answers(answers: list[dict[str, Any]], **known: Any) -> CaseFacts:
    """Build CaseFacts from stored call answers plus the facts the caller computed. The order
    facts need every answer's turn; if any answer lacks one, they are unknown."""
    timed = all(isinstance(a.get("turn_id"), int) for a in answers)
    return CaseFacts(
        recording_consent=values(answers, "recording_consent"),
        company_named=values(answers, "company_named"),
        change_requested=values(answers, "change_requested"),
        speaker_named=bool(values(answers, "speaker_name")),
        speaker_role=values(answers, "speaker_role"),
        flags=values(answers, "flag"),
        offered_number=bool(values(answers, "offered_number")),
        digits_typed=digits_typed(answers),
        consent_first=consent_first(answers) if timed else None,
        answers_in_order=answers_in_order(answers) if timed else None,
        same_person_yes_and_digits=same_person(answers) if timed else None,
        **known,
    )
