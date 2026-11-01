"""Sentences for the case timeline, one template per event type.

Every sentence that explains what happened comes from here, filled only with values from
the event itself, never from a model (design, "Dashboard"). The words are "provable" or
"not provable", never "safe". Text a person typed (a release reason) is shown in quotes
and escaped by the dashboard.
"""
from __future__ import annotations

from typing import Any

OUTCOME = {"verified": "Verified", "not_verified": "Not verified", "needs_review": "Needs review"}

LAST_FOUR = {
    "match": "matched the new account",
    "matches_old_account": "matched the old account, not the new one",
    "near_miss": "were one digit off the new account",
    "mismatch": "did not match the new account",
    "not_provided": "were not given",
    "ambiguous": "could not prove anything (the old and new accounts end the same)",
    "inconsistent": "were given two different ways",
}

CALL_RESULT = {
    "no_answer": "nobody answered",
    "voicemail": "it reached voicemail",
    "busy": "the line was busy",
    "failed": "the call failed to connect",
    "machine": "a machine answered",
    "declined": "the person declined to continue",
}


# Written by the worker when the voice service refuses a call (worker.place_call), and the
# refusal codes from Sami's internal.yaml (PreDialRefusal.code).
PRE_DIAL = "pre-dial check refused:"
PRE_DIAL_REASON = {
    "case_not_held": "the case was not held",
    "case_finished": "the case had already finished",
    "number_not_selected": "no number was selected",
    "number_opted_out": "the number opted out of calls",
    "outside_calling_window": "outside the vendor's calling hours",
    "attempt_cap_reached": "the case has used its call attempts",
    "number_cap_reached": "the number has had its calls this month",
    "not_on_dial_allowlist": "the number is not on the demo's dial allowlist",
    "voice_not_configured": "no voice service is configured",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def summary(event_type: str, body: dict[str, Any], actor_name: str) -> str:
    b = body or {}
    match event_type:
        case "change_detected":
            return f"A bank-detail change was detected for this vendor (via {b.get('detected_via', 'Ramp')})."
        case "hold_confirmed":
            n = int(b.get("bills_held", 0))
            return f"Payments to this vendor are on hold in Ramp; {_plural(n, 'unpaid bill')} had their payment dates saved."
        case "hold_failed":
            return "The hold could not be confirmed in Ramp, so a person must review this change."
        case "number_resolved":
            return "The Trust Ledger found a provable phone number for this vendor."
        case "no_provable_number":
            return "No phone number for this vendor is provable, so no call was made and a person must verify."
        case "call_started":
            return f"Call attempt {b.get('attempt', '?')} started."
        case "call_ended":
            attempt = b.get("attempt", "?")
            if b.get("result") == "answers_recorded":
                digits = LAST_FOUR.get(b.get("last_four_result", ""), "were not compared")
                return f"Call attempt {attempt} ended with answers recorded; the digits given {digits}."
            return f"Call attempt {attempt} ended: {CALL_RESULT.get(b.get('result', ''), 'no answers were recorded')}."
        case "call_timeout":
            return "The call never reported back, so a person must review this change."
        case "retry_timer_fired":
            return "Calling again inside the vendor's calling window."
        case "decision_made":
            return f"Code decided: {OUTCOME.get(b.get('outcome', ''), 'Needs review')}."
        case "review_cleared":
            return f"{actor_name} cleared the review: “{b.get('reason', '')}”"
        case "review_blocked":
            return f"{actor_name} blocked the change: “{b.get('reason', '')}”"
        case "release_co_approved":
            return f"{actor_name} gave the first of two approvals: “{b.get('reason', '')}”"
        case "release_approved":
            return f"{actor_name} approved the release: “{b.get('reason', '')}”"
        case "release_rejected":
            return f"{actor_name} rejected the release: “{b.get('reason', '')}”"
        case "hold_release_confirmed":
            n = len(b.get("restored_schedule") or [])
            return f"The hold was lifted in Ramp and {_plural(n, 'payment date')} restored."
        case "deadline_reached":
            return "Three business days passed without a decision; the hold stays on and a person must review."
        case "command_failed":
            error = str(b.get("last_error", ""))
            if b.get("command") == "place_call" and error.startswith(PRE_DIAL):
                code = error.removeprefix(PRE_DIAL).strip() or "unknown"
                return (f"The voice service checked the call and did not place it ({PRE_DIAL_REASON.get(code, code)}); "
                        "the hold stays on and a person must review.")
            return f"A step kept failing ({b.get('command', 'unknown')}); the hold stays on and a person must review."
        case _:
            return "An event was recorded."
