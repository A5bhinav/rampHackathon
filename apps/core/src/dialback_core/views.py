"""What the case page shows about the call and the decision (spec 0035).

Everything here reads what the system already recorded: the evidence chain's call events and
the rules the engine wrote down. Nothing is decided again (R3): the checklist is read from the
recorded rule ids, never by re-running a condition. Sentences are the rule table's own.
"""
from __future__ import annotations

from typing import Any

from dialback_decision import rules
from dialback_decision.engine import ENGINE_VERSION, TEMPLATES
from dialback_policy.script import ORDER, PHASE_SLOT, QUESTIONS, Script

CONDITIONS = (*rules.VERIFIED, *rules.STRICT)
_BLOCK = {**{r.id: "input" for r in rules.BLOCK0}, **{r.id: "not_verified" for r in rules.NOT_VERIFIED},
          **{r.id: "needs_review" for r in rules.NEEDS_REVIEW}, **{c.id: "verified" for c in CONDITIONS},
          **{c.unmet_id: "not_met" for c in CONDITIONS}, rules.NOT_MET: "not_met",
          rules.INVALID: "engine", rules.ENGINE_ERROR: "engine"}

NOTES = {
    "verified": "Every condition for Verified held, so code said Verified.",
    "not_met": "No rule against the change fired, but not every condition for Verified held.",
    "not_verified": "A Not verified rule matched first, so the Verified checklist was not run.",
    "needs_review": "A Needs review rule matched first, so the Verified checklist was not run.",
    "input": "The call's answers couldn't count as evidence (an input problem), so the Verified checklist was not run.",
    "engine": "The decision engine hit an error or unreadable facts, so the Verified checklist was not run.",
}

EFFECTS = {
    "verified": "The hold stays on until an approver who didn't enter this change releases it, with a written reason.",
    "verified_unknown": "The hold stays on until two different approvers release it, each with a written reason: nobody "
                        "at Dialback is known to have entered this change.",
    "not_verified": "The hold stays on and the case is blocked: nobody can release payments to these bank details. "
                    "Only a new bank change reopens it.",
    "needs_review": "The hold stays on. An approver must review: clearing it sends the case to a second approver to "
                    "release, blocking it keeps the hold on.",
}
FALLBACK_EFFECT = "The hold stays on and a person must review this change."


def fired(ids: list[str]) -> list[dict[str, str]]:
    """Each recorded rule with its sentence and the block it belongs to; ids this engine doesn't
    know (earlier versions) are 'retired'."""
    return [{"id": i, "sentence": TEMPLATES.get(i, i), "block": _BLOCK.get(i, "retired")} for i in ids]


def checklist(outcome: str, ids: list[str], engine_version: str, strict: bool = False) -> tuple[list[dict[str, str]], str]:
    """The Verified conditions as the recorded decision leaves them: passed, not met or not checked.
    `strict` is the decision_made body's strict_mode; a recorded S01 id implies it too."""
    strict = strict or any(i.startswith("S01_") for i in ids)
    conditions = [c for c in CONDITIONS if strict or c not in rules.STRICT]
    def item(c: rules.Rule, status: str) -> dict[str, str]:
        return {"id": c.id, "sentence": c.unmet if status == "not_met" and c.unmet else c.template, "status": status}
    if engine_version != ENGINE_VERSION:
        return ([item(c, "not_checked") for c in conditions],
                f"This decision was made by {engine_version}, whose rules differ from today's, so the checklist is not shown as passed.")
    if outcome == "verified" and all(c.id in ids for c in conditions):
        return [item(c, "passed") for c in conditions], NOTES["verified"]
    if rules.NOT_MET in ids:
        unmet = {i.removesuffix("_unmet") for i in ids if i.endswith("_unmet")}
        return [item(c, "not_met" if c.id in unmet else "passed") for c in conditions], NOTES["not_met"]
    first = next((_BLOCK.get(i) for i in ids if _BLOCK.get(i) in NOTES), "engine")
    return [item(c, "not_checked") for c in conditions], NOTES[first]


def effect(outcome: str, unknown_changer: bool = False) -> str:
    if outcome == "verified" and unknown_changer:
        return EFFECTS["verified_unknown"]  # ADR-10: release_co_approved first
    return EFFECTS.get(outcome, FALLBACK_EFFECT)


OFFERED = "A phone number (not shown)"


def answer_view(stored: dict[str, Any], current: bool = True) -> dict[str, Any]:
    """One stored answer for the page. account_last4 becomes a digit count, or dont_know / refused:
    the keyed hash never leaves the core, and no other string is passed through for it. A number
    the callee offered is not shown either: it could be anyone's. `current` is False for an
    earlier attempt, whose answers count only against the change."""
    slot = stored.get("slot", "")
    value, count = stored.get("value"), None
    if isinstance(value, dict):
        n = value.get("digit_count")
        count = n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 4 else None
        value = None
    elif not isinstance(value, str) or (slot == "account_last4" and value not in ("dont_know", "refused")):
        value = None
    elif slot == "offered_number":
        value = OFFERED
    return {"slot": slot, "value": value, "digit_count": count, "turn_id": stored.get("turn_id", 0),
            "capture_method": stored.get("capture_method", "speech"), "captured_at": str(stored.get("captured_at", "")),
            "feeds": list((rules.SLOT_RULES if current else rules.EARLIER_SLOT_RULES).get(slot, ()))}


CALL_ENDINGS = {
    "answers_recorded": "The person answered the questions.",
    "no_answer": "Nobody answered.", "voicemail": "It reached voicemail.",
    "busy": "The line was busy.", "failed": "The call failed to connect.", "machine": "A machine answered.",
    "declined": "The person declined to continue on a recorded line.",
    "callback_requested": "The person asked to be called back later.",
    "timed_out": "No report came back from the call in time, so a person must review.",
}


def calls(events: list[tuple[str, Any, dict[str, Any]]], voice: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Attempts from (type, occurred_at, body) evidence, oldest first. A call_started opens one;
    its call_ended or call_timeout closes it. `voice` adds voice.calls facts by call id."""
    out: dict[str, dict[str, Any]] = {}
    for kind, at, body in events:
        call_id = str(body.get("call_id") or "")
        if kind == "call_started" and call_id:
            row = voice.get(call_id, {})
            out[call_id] = {"call_id": call_id, "attempt": body.get("attempt", 1), "started_at": at, "ended_at": None,
                            "result": None, "reason": None, "answered_by": None, "direction": None,
                            "dialed_number_is_pick": None, "provider": row.get("provider"), "agent_version": None,
                            "last_four_result": None, "answers": [], "checks": None}
        elif kind == "call_timeout" and not call_id:
            call_id = next((k for k, c in reversed(out.items()) if c["result"] is None), "")
        if call_id in out and kind in ("call_ended", "call_timeout"):
            c = out[call_id]
            result = body.get("result") if kind == "call_ended" else "timed_out"
            answers = (body.get("answers") or {}).get("answers") or []
            c.update(ended_at=at, result=result, reason=CALL_ENDINGS.get(result),
                     answered_by=body.get("answered_by"), direction=body.get("direction"),
                     dialed_number_is_pick=body.get("dialed_number_is_pick"), agent_version=body.get("agent_version"),
                     last_four_result=body.get("last_four_result"), answers=answers, checks=body.get("checks"))
    attempts = list(out.values())
    last = max((i for i, c in enumerate(attempts) if c["answers"]), default=-1)  # the call the decision read
    for i, c in enumerate(attempts):
        c["answers"] = [answer_view(a, current=i == last) for a in c["answers"]]
    return attempts


def call_script() -> dict[str, Any]:
    """The call policy's fixed text, with the customer's name and AP callback number as labels,
    so no phone number leaves the core."""
    s = Script(customer_name="[your company]", callback_number="[your AP callback number]")
    return {"disclosure": s.disclosure(),
            "questions": [{"slot": PHASE_SLOT[p], "wording": QUESTIONS[p].wording(s)} for p in ORDER],
            "note": "Dialback keeps no transcript of the call. What it keeps is each answer as code recorded it, with the "
                    "turn of the conversation it came from, and the script checks computed from the call. For the "
                    "account's last four digits it keeps how many were given and a keyed fingerprint in the evidence "
                    "log, never the digits; this page shows only the count."}
