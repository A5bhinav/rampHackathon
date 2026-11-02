"""CaseFacts: everything decide() looks at, from three sources (design, "Decision engine").

1. Telephony: whether a person answered and whether we dialed the Trust Ledger's pick.
2. Recorded answers: the stored call-answers shape (Sami's contract), never raw digits.
3. Computed by code after the call: the last-four result, the company match, the voice
   service's checks, and two facts that need other slices' data (see `None` below).

`None` means "not known". The engine treats the two facts no slice can supply yet as
unchecked and says so in the decision (engine.py); every other unknown fails closed.
"""
from __future__ import annotations

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
class CaseFacts:
    call_result: str                               # answers_recorded, no_answer, voicemail, ...
    dialed_eligible_number: bool                   # the ledger picked the number and we dialed out
    recording_consent: str | None = None           # yes | no
    company_named: str | None = None
    change_requested: str | None = None            # yes | no | unsure
    speaker_named: bool = False
    flags: tuple[str, ...] = ()
    offered_number: bool = False                   # the callee asked us to call another number
    last_four_result: str | None = None
    company_matches: bool | None = None            # computed by the core against the vendor's names
    checks: CallChecks | None = None               # computed by the voice service
    # Facts that need other slices' data. None until those slices supply them.
    speaker_in_change_thread: bool | None = None   # needs the ledger's change context (Inesh)
    same_person_yes_and_digits: bool | None = None # needs per-turn speaker identity (Sami)
    offered_number_from_request: bool | None = None  # needs the ledger's change context (Inesh)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["flags"] = sorted(self.flags)
        return data


def summarize_answers(answers: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Group stored answers by slot, in recorded order."""
    by_slot: dict[str, list[Any]] = {}
    for a in answers:
        by_slot.setdefault(a["slot"], []).append(a["value"])
    return by_slot


def facts_from_answers(answers: list[dict[str, Any]], **known: Any) -> CaseFacts:
    """Build CaseFacts from stored call answers plus facts the caller computed.

    A slot answered twice with different values counts as unknown (it can't verify anything).
    """
    by_slot = summarize_answers(answers)

    def single(slot: str) -> Any:
        values = by_slot.get(slot, [])
        return values[0] if values and all(v == values[0] for v in values) else None

    return CaseFacts(
        recording_consent=single("recording_consent"),
        company_named=single("company_named"),
        change_requested=single("change_requested"),
        speaker_named=single("speaker_name") is not None,
        flags=tuple(by_slot.get("flag", [])),
        offered_number=bool(by_slot.get("offered_number")),
        **known,
    )
