"""The facts decide() reads for one call report (spec 0048, A1; a draft for Abhi's review, Q102).

Everything `Worker.decide` does after its database reads, as a pure function, so the attack lab can
chain production's own fact assembly instead of a copy: whether the dialed number was this
version's pick, the earlier calls of this generation, the company match with trade names, and
`facts_from_answers`. The worker calls it; its behavior is unchanged.
"""
from __future__ import annotations

from typing import Any

from dialback_decision import CallChecks, CaseFacts
from dialback_decision.facts import earlier_call, facts_from_answers, values

from .company import every_name_matches
from .ports import ChangeFacts


def call_facts(report: dict[str, Any], case: dict[str, Any], generation: list[dict[str, Any]],
               context: ChangeFacts) -> CaseFacts:
    """`report` is the stored call_ended body (the voice service's report plus the core's
    last_four_result); `case` the case row (legal_name, trade_names); `generation` this version's
    number_resolved and call_ended events in order, each {"type", "body"}; `context` the ledger's
    change-context result."""
    answers = (report.get("answers") or {}).get("answers") or []
    picks = [e["body"].get("contact_point_id") for e in generation if e["type"] == "number_resolved"]
    # Only this version's pick counts, and a report that doesn't name it isn't eligible (D1).
    dialed_eligible = bool(picks) and report.get("contact_point_id") == picks[-1]
    # Earlier calls can count against the change, never for it (D2).
    earlier = tuple(earlier_call(e["body"]) for e in generation
                    if e["type"] == "call_ended" and e["body"].get("call_id") != report.get("call_id"))
    checks = report.get("checks")
    return facts_from_answers(
        answers,
        call_result=report["result"],
        dialed_eligible_number=dialed_eligible,
        last_four_result=report.get("last_four_result"),
        company_matches=every_name_matches(values(answers, "company_named"), case["legal_name"], case["trade_names"] or ()),
        checks=CallChecks(**checks) if checks else None,
        earlier_calls=earlier,
        dialed_out=None if report.get("direction") is None else report["direction"] == "outbound",
        dialed_number_is_pick=report.get("dialed_number_is_pick"),
        answered_by=report.get("answered_by"),
        speaker_in_change_thread=context.speaker_in_change_thread,
        offered_number_from_request=context.offered_number_from_request,
        speaker_known_before_request=context.speaker_known_before_request,
    )
