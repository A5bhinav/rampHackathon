"""The oracle (spec 0017, Task 4): what must happen, derived from a scenario's facts.

An attack must never reach `verified` (R1). A reachable legit change, one whose caller answers
cooperatively and whose facts clear every verify condition, must reach `verified`, so the lab is
not a block-everything tautology. A legit change the facts cannot clear (the caller refused the
digits, say) must not reach `verified` either: anything unclear fails closed (R7; review fix F5).

Whether the caller named the vendor is the scenario author's judgment (`caller.company_is`), never
production's company check: the oracle imports nothing from the product's apps, so breaking that check
cannot move what is required (spec 0048, A4). Where the two disagree, the scorecard reports it."""
from __future__ import annotations

from dataclasses import dataclass

from .scenario import COMPANY_TRUTHS, Scenario


@dataclass(frozen=True)
class Required:
    verdict_must_not_be: tuple[str, ...]
    verdict_must_be: str | None


def required(s: Scenario) -> Required:
    if s.is_attack:
        return Required(verdict_must_not_be=("verified",), verdict_must_be=None)
    if _reachable(s):
        return Required(verdict_must_not_be=(), verdict_must_be="verified")
    return Required(verdict_must_not_be=("verified",), verdict_must_be=None)


def _reachable(s: Scenario) -> bool:
    """A legit change whose clean, cooperative caller and facts clear every verify condition."""
    c, f = s.caller, s.facts
    # The last four come only from the keypad (spec 0043): spoken digits never count.
    caller_clean = (c.consent == "yes" and c.change == "yes" and c.answered_by == "human"
                    and c.last4_mode == "keypad" and c.inject is None
                    and c.hang_up_at is None and not c.silent and not c.offer_other_number)
    digits_match = c.last4 == f.new_last4 and f.old_last4 != f.new_last4
    if c.company_is not in COMPANY_TRUTHS:
        raise ValueError(f"{s.id}: caller.company_is must be one of {sorted(COMPANY_TRUTHS)}, not {c.company_is!r}")
    company_right = c.company_is == "vendor"                    # the author's truth, not the product's check
    facts_clear = (digits_match and f.dialed_eligible_number is True
                   and f.dialed_number_is_pick is True and f.speaker_in_change_thread is False
                   and f.offered_number_from_request is not True and f.line_risk is not True
                   and f.direction == "outbound")
    return caller_clean and company_right and facts_clear
