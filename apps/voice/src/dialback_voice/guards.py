"""Pre-dial checks (design, "How a call runs", step 1): before the voice service dials, code
confirms the call is allowed. This is the pure decision; the POST /calls handler gathers the
facts (the case from app.cases, the pick from the Trust Ledger, the call history, the clock) and
asks here, so the rule is tested without a database. A failing check is a PreDialRefusal code
from internal.yaml, and the core sends the case to needs_review (R7)."""
from __future__ import annotations

from dataclasses import dataclass

# PreDialRefusal.code (internal.yaml), checked in this order.
CASE_NOT_HELD = "case_not_held"
CASE_FINISHED = "case_finished"
NUMBER_NOT_SELECTED = "number_not_selected"
NUMBER_OPTED_OUT = "number_opted_out"
OUTSIDE_CALLING_WINDOW = "outside_calling_window"
ATTEMPT_CAP_REACHED = "attempt_cap_reached"
NUMBER_CAP_REACHED = "number_cap_reached"
NOT_ON_DIAL_ALLOWLIST = "not_on_dial_allowlist"

ALL_CODES: tuple[str, ...] = (CASE_NOT_HELD, CASE_FINISHED, NUMBER_NOT_SELECTED, NUMBER_OPTED_OUT,
                              OUTSIDE_CALLING_WINDOW, ATTEMPT_CAP_REACHED, NUMBER_CAP_REACHED,
                              NOT_ON_DIAL_ALLOWLIST)

MAX_ATTEMPTS_PER_CASE = 3       # internal.yaml PlaceCallRequest.attempt is 1..3
MAX_CALLS_PER_NUMBER_30D = 3    # design: at most 3 AI calls to one number per 30 days


@dataclass(frozen=True)
class PreDialInputs:
    """The facts the pre-dial decision needs, gathered by the POST /calls handler. Keeping them
    explicit keeps the rule pure and keeps this module clear of the Trust Ledger's table shapes,
    which are not final yet (spec 0012, Q40)."""
    case_exists: bool
    case_finished: bool          # the case reached its one finished state (released)
    case_held: bool              # the hold is in place and the case is in a state a call is valid for
    has_number_pick: bool        # the Trust Ledger selected a contact point for this case (R2)
    number_opted_out: bool
    within_calling_window: bool  # weekdays 9:00-16:30 vendor-local (demo window configurable)
    attempts_used: int           # calls already placed for this case
    number_calls_last_30d: int   # AI calls to this number in the last 30 days
    demo_dataset: bool = False   # a demo dataset also requires the dial allowlist
    number_on_allowlist: bool = True


def pre_dial_refusal(f: PreDialInputs) -> str | None:
    """The first failing check's code, or None when the call may be placed. Order matters: a
    finished or unheld case is refused before we look at the number, and the number before the
    clock, so the refusal names the hardest reason not to call."""
    if not f.case_exists:
        return CASE_NOT_HELD          # a call for a case we cannot find is refused, fail closed
    if f.case_finished:
        return CASE_FINISHED
    if not f.case_held:
        return CASE_NOT_HELD
    if not f.has_number_pick:
        return NUMBER_NOT_SELECTED    # R2: nothing provable to dial
    if f.number_opted_out:
        return NUMBER_OPTED_OUT
    if not f.within_calling_window:
        return OUTSIDE_CALLING_WINDOW
    if f.attempts_used >= MAX_ATTEMPTS_PER_CASE:
        return ATTEMPT_CAP_REACHED
    if f.number_calls_last_30d >= MAX_CALLS_PER_NUMBER_30D:
        return NUMBER_CAP_REACHED
    if not f.number_on_allowlist:     # AGENTS.md: never call outside the team allowlist, any dataset (spec 0030)
        return NOT_ON_DIAL_ALLOWLIST
    return None
