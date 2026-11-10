"""Spec 0048, B1: the call-report generator, and the company phrase bank.

A drawn `ReportCase` starts as an honest, full confirmation (consent and the change both "yes", the
vendor's name, the new account's last four, every check passing, a human on a call we dialed out to the
pick) and then has up to three things changed, each drawn from the domain's list below. That keeps most
reports close to verified, where F1 and F2 have something to judge; a fully random report almost never
gets near it. The changes cover every recorded value for each answer (an answer missing, recorded twice
with different values, or out of order included), digits set against the new and old accounts so that
every comparison result comes up, flags, an offered number, every call result, who answered, the
direction, each check failing, and the case's details (trade names, earlier calls, the speaker in the
change thread, a number offered from the request, a stale pick).

The company answers come from the hand-written phrase bank (`phrasebank.py`, Q105).
"""
from __future__ import annotations

import dataclasses

from dialback_decision.rules import FLAGS
from hypothesis import strategies as st

from .phrasebank import OTHERS, VENDORS, phrases
from .reports import CLEAN_CHECKS, FAILING_CHECKS, RELAY_RESULTS, STATUS_CALLS, ReportCase


def _answer(slot: str, value: str, turn: int, method: str = "speech") -> dict:
    return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"t{turn}_{slot}",
            "captured_at": f"2026-11-01T17:{turn:02d}:00Z", "capture_method": method}


def _honest(draw, domain: str) -> ReportCase:
    legal, trades = draw(st.sampled_from(VENDORS))
    new = f"{draw(st.integers(0, 9999)):04d}"
    old = None if draw(st.booleans()) else f"{(int(new) + draw(st.integers(1, 9999))) % 10000:04d}"   # never == new
    company = draw(st.sampled_from(phrases("vendor", legal, trades)))
    answers = (_answer("recording_consent", "yes", 1), _answer("company_named", company, 3),
               _answer("department", "Accounts payable", 5), _answer("speaker_name", "Jordan Lee", 7),
               _answer("speaker_role", "AP clerk", 9), _answer("change_requested", "yes", 11),
               _answer("account_last4", new, 13, "keypad"))
    return ReportCase(domain=domain, result="answers_recorded", answers=answers, checks=dict(CLEAN_CHECKS),
                      verified_setup=True, direction="outbound", answered_by="human",
                      dialed_number_is_pick=True if domain == "contract" else None, pick_current=True,
                      new_last4=new, old_last4=old, legal_name=legal, trade_names=trades, earlier=(),
                      speaker_in_change_thread=False, offered_number_from_request=False,
                      truth={"company": ["vendor"], "speaker_from_thread": False, "offered_from_request": False,
                             "earlier_concern": False})


# ---- the changes --------------------------------------------------------------------------------

def _set(case: ReportCase, slot: str, value: str) -> ReportCase:
    return dataclasses.replace(case, answers=tuple({**a, "value": value} if a["slot"] == slot else a
                                                   for a in case.answers))


def _drop(case: ReportCase, slot: str) -> ReportCase:
    truth = {**case.truth, "company": []} if slot == "company_named" else case.truth
    return dataclasses.replace(case, answers=tuple(a for a in case.answers if a["slot"] != slot), truth=truth)


def _add(case: ReportCase, slot: str, value: str, truth: str | None = None) -> ReportCase:
    turn = max((a["turn_id"] for a in case.answers), default=0) + 2
    changes = {"company": [*case.truth["company"], truth]} if slot == "company_named" else {}
    return dataclasses.replace(case, answers=(*case.answers, _answer(slot, value, turn)),
                               truth={**case.truth, **changes})


def _retime(case: ReportCase, slot: str, turn: int) -> ReportCase:
    return dataclasses.replace(case, answers=tuple({**a, "turn_id": turn} if a["slot"] == slot else a
                                                   for a in case.answers))


def _digits(case: ReportCase, value: str) -> ReportCase:
    return _set(case, "account_last4", value) if case.values("account_last4") else case


def _company(draw, case: ReportCase, truth: str) -> ReportCase:
    said = draw(st.sampled_from(phrases(truth, case.legal_name, case.trade_names, draw(st.sampled_from(OTHERS)))))
    case = _set(case, "company_named", said)
    return dataclasses.replace(case, truth={**case.truth, "company": [truth] * len(case.truth["company"])})


def _matches_old(draw, case: ReportCase) -> ReportCase:
    old = case.old_last4 if case.old_last4 not in (None, case.new_last4) else \
        f"{(int(case.new_last4) + 5000) % 10000:04d}"
    return _digits(dataclasses.replace(case, old_last4=old), old)


def _near(value: str) -> str:
    return value[:-1] + str((int(value[-1]) + 1) % 10)


_EARLIER_PLAIN = ("no_answer", "failed", "callback_requested", "busy", "voicemail", "machine")


def _earlier(draw, case: ReportCase, with_answers: bool) -> ReportCase:
    n = len(case.earlier) + 1
    body = {"call_id": f"00000000-0000-0000-0000-{n:012d}", "result": draw(st.sampled_from(_EARLIER_PLAIN))}
    concern = False
    if with_answers:                                   # contract only (FINDINGS A16)
        kind = draw(st.sampled_from(["clean", "unsure", "flag", "consent_no", "near_miss", "denial"]))
        consent, change = ("no" if kind == "consent_no" else "yes"), {"unsure": "unsure", "denial": "no"}.get(kind, "yes")
        answers = [_answer("recording_consent", consent, 1), _answer("change_requested", change, 3)]
        if kind == "flag":
            answers.append(_answer("flag", draw(st.sampled_from(sorted(FLAGS))), 3))
        body["answers"] = {"schema_version": "call-answers/v0", "call_id": body["call_id"], "answers": answers}
        if kind == "near_miss":
            body["last_four_result"] = "near_miss"
        concern = kind != "clean"
    return dataclasses.replace(case, earlier=(*case.earlier, body),
                               truth={**case.truth, "earlier_concern": case.truth["earlier_concern"] or concern})


def _other_result(draw, case: ReportCase) -> ReportCase:
    """Another way the call ended. Reachable: a verified relay call's other results, or a call that never
    connected (the status callback; no answers, checks or direction reach the report)."""
    if case.domain == "contract":
        return dataclasses.replace(case, result=draw(st.sampled_from(
            ["no_answer", "voicemail", "busy", "failed", "machine", "declined", "callback_requested"])))
    if case.verified_setup and draw(st.booleans()):
        return dataclasses.replace(case, result=draw(st.sampled_from(RELAY_RESULTS[1:])))
    result = draw(st.sampled_from(sorted(STATUS_CALLS)))
    return dataclasses.replace(case, result=result, verified_setup=False, checks=None, direction=None,
                               answered_by="machine" if result == "machine" else None)


def _unverified(draw, case: ReportCase) -> ReportCase:
    """A socket the relay could not verify (a forged setup): it reports `failed` with no connect facts."""
    return dataclasses.replace(case, result="failed", verified_setup=False, checks=None, direction=None,
                               answered_by=None)


CHANGES = {
    "consent_no": lambda d, c: _set(c, "recording_consent", "no"),
    "consent_missing": lambda d, c: _drop(c, "recording_consent"),
    "consent_twice": lambda d, c: _add(c, "recording_consent", "no"),
    "consent_late": lambda d, c: _retime(c, "recording_consent", 4),               # after the company answer
    "change_no": lambda d, c: _set(c, "change_requested", "no"),
    "change_unsure": lambda d, c: _set(c, "change_requested", "unsure"),
    "change_missing": lambda d, c: _drop(c, "change_requested"),
    "change_twice": lambda d, c: _add(c, "change_requested", d(st.sampled_from(["no", "unsure"]))),
    "change_with_consent": lambda d, c: _retime(c, "change_requested", 1),         # one "yes" for both
    "change_before_name": lambda d, c: _retime(c, "change_requested", 6),
    "digits_before_change": lambda d, c: _retime(c, "account_last4", 10),
    "digits_old": _matches_old,
    "digits_near_miss": lambda d, c: _digits(c, _near(c.new_last4)),
    "digits_mismatch": lambda d, c: _digits(c, f"{(int(c.new_last4) + 4321) % 10000:04d}"),
    "digits_dont_know": lambda d, c: _digits(c, "dont_know"),
    "digits_refused": lambda d, c: _digits(c, "refused"),
    "digits_short": lambda d, c: _digits(c, c.new_last4[:2]),
    "digits_ambiguous": lambda d, c: dataclasses.replace(c, old_last4=c.new_last4),
    "digits_twice": lambda d, c: _add(c, "account_last4", _near(c.new_last4)),
    "digits_missing": lambda d, c: _drop(c, "account_last4"),
    **{f"company_{t}": (lambda d, c, t=t: _company(d, c, t))
       for t in ("other", "negation", "reseller", "correction", "partial")},
    "company_missing": lambda d, c: _drop(c, "company_named"),
    "company_twice": lambda d, c: _add(c, "company_named", d(st.sampled_from(OTHERS)), "other"),
    "name_missing": lambda d, c: _drop(c, "speaker_name"),
    "name_twice": lambda d, c: _add(c, "speaker_name", "Sam Ortiz"),
    "role_missing": lambda d, c: _drop(c, "speaker_role"),
    "role_twice": lambda d, c: _add(c, "speaker_role", "Controller"),
    "flag": lambda d, c: _add(c, "flag", d(st.sampled_from(sorted(FLAGS)))),
    "offered_number": lambda d, c: _add(c, "offered_number", "(312) 555-0142"),
    "answerer_machine": lambda d, c: dataclasses.replace(c, answered_by="machine"),
    "answerer_unknown": lambda d, c: dataclasses.replace(c, answered_by=None),
    "direction_unknown": lambda d, c: dataclasses.replace(c, direction=None),
    "other_result": _other_result,
    "check_fails": lambda d, c: (dataclasses.replace(c, checks={**c.checks, (k := d(st.sampled_from(sorted(CLEAN_CHECKS)))):
                                                                 FAILING_CHECKS[k]}) if c.checks else c),
    "speaker_from_thread": lambda d, c: dataclasses.replace(c, speaker_in_change_thread=True,
                                                            truth={**c.truth, "speaker_from_thread": True}),
    "speaker_unknown": lambda d, c: dataclasses.replace(c, speaker_in_change_thread=None,
                                                        truth={**c.truth, "speaker_from_thread": d(st.booleans())}),
    # The ledger can only place a number the caller offered (A3), so the caller offers one here too.
    "offered_from_request": lambda d, c: dataclasses.replace(_add(c, "offered_number", "(645) 555-0177"),
                                                             offered_number_from_request=True,
                                                             truth={**c.truth, "offered_from_request": True}),
    "offered_unknown": lambda d, c: dataclasses.replace(c, offered_number_from_request=None),
    "pick_stale": lambda d, c: dataclasses.replace(c, pick_current=False),
    "earlier_plain": lambda d, c: _earlier(d, c, with_answers=False),
}
def _consent_refused(d, c: ReportCase) -> ReportCase:
    """Consent refused: the call ends there, so the consent answer is all it records."""
    consent = [{**a, "value": "no"} for a in c.answers if a["slot"] == "recording_consent"]
    return dataclasses.replace(c, answers=tuple(consent), truth={**c.truth, "company": []})


_ORDER = ("recording_consent", "company_named", "department", "speaker_name", "speaker_role", "change_requested",
          "account_last4")


def _ended_early(d, c: ReportCase) -> ReportCase:
    """The call ended before a question was answered (a hang-up, or an answer still unclear after one more
    ask): every question from there on goes unanswered, as production records it."""
    cut = d(st.sampled_from(_ORDER))
    gone = set(_ORDER[_ORDER.index(cut):])
    kept = tuple(a for a in c.answers if a["slot"] not in gone and not (a["slot"] == "flag" and cut == _ORDER[0]))
    truth = {**c.truth, "company": []} if "company_named" in gone else c.truth
    return dataclasses.replace(c, answers=kept, truth=truth)


def _pressure_flag(d, c: ReportCase) -> ReportCase:
    """Code's own flag (step.py): pressure_to_approve, citing a callee turn at or after consent."""
    consent = [a["turn_id"] for a in c.answers if a["slot"] == "recording_consent"]
    turns = sorted({a["turn_id"] for a in c.answers if a["slot"] != "flag" and consent and a["turn_id"] >= consent[0]})
    if not turns or c.values("flag") or c.values("recording_consent") != ["yes"]:     # code flags only after a yes
        return c
    flag = {"slot": "flag", "value": "pressure_to_approve", "turn_id": d(st.sampled_from(turns)),
            "tool_call_id": "code_pressure", "captured_at": "2026-11-01T17:30:00Z", "capture_method": "speech"}
    return dataclasses.replace(c, answers=(*c.answers, flag))


# Spec 0048 review fix 4: the reachable domain draws only what production's call records (only the current
# question's answer, in the script's order, nothing after a refused consent, code's pressure flag and no other,
# no offered number). Every other change is contract-only.
RECORDABLE = ("change_no", "change_unsure", "digits_old", "digits_near_miss", "digits_mismatch", "digits_dont_know",
              "digits_refused", "digits_short", "digits_ambiguous", "company_other", "company_negation",
              "company_reseller", "company_correction", "company_partial", "answerer_machine", "answerer_unknown",
              "direction_unknown", "other_result", "check_fails", "speaker_from_thread", "speaker_unknown",
              "offered_unknown", "pick_stale", "earlier_plain")
REACHABLE_ONLY = {"unverified": _unverified, "consent_refused": _consent_refused, "ended_early": _ended_early,
                  "pressure_flag": _pressure_flag}
CONTRACT_ONLY = {
    "inbound": lambda d, c: dataclasses.replace(c, direction="inbound"),
    "pick_false": lambda d, c: dataclasses.replace(c, dialed_number_is_pick=False),
    "earlier_with_answers": lambda d, c: _earlier(d, c, with_answers=True),
    "checks_missing": lambda d, c: dataclasses.replace(c, checks=None),
}


@st.composite
def report_cases(draw, domain: str) -> ReportCase:
    """One report case in `domain` (reachable | contract): an honest confirmation and up to three changes."""
    if domain not in ("reachable", "contract"):
        raise ValueError(f"unknown domain {domain!r}")
    case = _honest(draw, domain)
    changes = ({**{k: CHANGES[k] for k in RECORDABLE}, **REACHABLE_ONLY} if domain == "reachable"
               else {**CHANGES, **CONTRACT_ONLY})
    count = draw(st.sampled_from([0, 1, 1, 2, 3]))
    for name in draw(st.lists(st.sampled_from(sorted(changes)), min_size=count, max_size=count)):
        case = changes[name](draw, case)
    return case
