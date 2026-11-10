"""Spec 0048, part B: the call-report fuzzer (`lab reports`): how answers become facts and outcomes.

No call is simulated. A `ReportCase` is the voice service's call result and the case's details, with the
generator's truth about them (`reportgen.py` draws them: an honest, full confirmation with up to three
things changed). `run` turns it into the report production would store and the facts `decide()` reads,
by A1's chain:

- **Reachable today** (`domain="reachable"`): what production can send. A call that connected goes
  through the voice service's `report_body`, then `with_connect_facts` when the relay verified the
  socket, so answers ride along only on `answers_recorded` and the pick is always true. A call that
  never connected is reported the way the status callback does: production's `status_result` maps
  Twilio's status and machine detection, and the body carries no answers, checks or direction.
- **Contract only** (`domain="contract"`): bodies `internal.yaml` allows that production never sends
  today, such as answers on a call that did not end `answers_recorded` (FINDINGS A16), an inbound
  direction, a dialed number reported as not the pick, and earlier calls that carry answers.

Both then run the core's own check of a report (`CallEndedRequest`), its digit comparison
(`spoken_last_four`, `compare_last_four`) and `call_facts`. Only the reachable domain is gated; a
violation seen only in the contract-only domain is a future-state finding for its owner.

The checks: F1 (verified only on an honest, full confirmation, judged by the generator's truth), F2 (less
certainty never helps: no move in `MOVES` turns an outcome that isn't verified into verified) and F3
(the company check against the phrase bank's truth). `doubles` holds the broken stand-ins each check
is first shown failing on.

Since spec 0082 D7b (Sami, Nov 10) the company answer no longer has to name the vendor: only a refusal
(D7a: "Not Acme", "a reseller for Acme", "I used to work at Acme") is False and flags the call (NR03). So
F1 asks for no refusal in the company answer, F3 gates only the refusal classes (each must be False), F2's
company_other never replaces a refusal, and two company moves are retired (`RETIRED_MOVES`).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import itertools
import json
import re
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import dialback_core.answers as core_answers
import dialback_core.call_facts as core_facts
import dialback_decision
import dialback_voice.app as voice_app
from dialback_core.calls import CallEndedRequest
from dialback_core.company import every_name_matches
from dialback_core.ports import ChangeFacts
from dialback_decision import Decision, rules
from dialback_decision.facts import CaseFacts
from dialback_policy.step import End
from dialback_voice.store import CallDial
from dialback_voice.transport import CallResult
from dialback_voice.twilio.origination import status_result

from .phrasebank import REFUSAL_CLASSES, refuses

CASE_ID, CALL_ID = UUID(int=0x0048_B001), UUID(int=0x0048_B002)
PICK, NEWER_PICK = UUID(int=0x0048_B003), UUID(int=0x0048_B004)
RETRY_AT = dt.datetime(2026, 11, 2, 16, 0, tzinfo=dt.UTC)
CALL_RESULTS = ("answers_recorded", "no_answer", "voicemail", "busy", "failed", "machine", "declined",
                "callback_requested")
RELAY_RESULTS = ("answers_recorded", "callback_requested", "declined", "failed", "no_answer", "voicemail")
# Twilio's (CallStatus, AnsweredBy) for each result the status callback reports (status_result maps them).
STATUS_CALLS = {"machine": ("completed", "machine_start"), "no_answer": ("no-answer", None),
                "busy": ("busy", None), "failed": ("failed", None)}
CLEAN_CHECKS = {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
                "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}
FAILING_CHECKS = {"disclosure_first": False, "answers_grounded": False, "agent_spoke_account_digits": True,
                  "new_number_accepted": True, "policy_violations": 1, "speech_guard_trips": 1}
COMPANY_TRUTHS = ("vendor", "other", "negation", "reseller", "correction", "partial")
NAMES_SOMEONE_ELSE = ("other", "negation", "reseller", "correction")    # other-company acceptance (spec 0082, D10)


@dataclasses.dataclass(frozen=True)
class ReportCase:
    domain: str                              # reachable | contract
    result: str                              # the call's result (CallEndedBody.result)
    answers: tuple[dict, ...]                # the recorded answers, transit shape (digits in clear)
    checks: dict | None                      # the voice service's checks; None when none were computed
    verified_setup: bool                     # reachable: the relay verified the socket (with_connect_facts ran)
    direction: str | None                    # Twilio's direction: outbound | inbound | None (unknown)
    answered_by: str | None                  # Twilio's machine detection: human | machine | None (unknown)
    dialed_number_is_pick: bool | None       # contract only: what the report says (reachable: production's)
    pick_current: bool                       # the generation: the dial named this version's latest pick
    new_last4: str
    old_last4: str | None
    legal_name: str
    trade_names: tuple[str, ...]
    earlier: tuple[dict, ...]                # earlier call_ended bodies of this generation
    speaker_in_change_thread: bool | None    # the change-context result (the ledger may not know: None)
    offered_number_from_request: bool | None
    truth: dict                              # company (one truth per company answer), speaker_from_thread,
                                             # offered_from_request, earlier_concern

    def values(self, slot: str) -> list[Any]:
        return [a["value"] for a in self.answers if a["slot"] == slot]


@dataclasses.dataclass(frozen=True)
class Ran:
    body: dict[str, Any]                     # the voice service's report (the request the core receives)
    report: dict[str, Any]                   # what the core stores: digits compared (not hashed)
    facts: CaseFacts
    decision: Decision


def _answers_object(case: ReportCase) -> dict:
    return {"schema_version": "call-answers/v0", "call_id": str(CALL_ID), "answers": [dict(a) for a in case.answers]}


def body_of(case: ReportCase) -> dict[str, Any]:
    """The report body, by production's code for the reachable domain (see the module docstring)."""
    if case.domain == "reachable":
        if not case.verified_setup and case.result not in STATUS_CALLS:
            raise ValueError(f"unreachable: an unverified socket reports failed, not {case.result}")
        dial = CallDial(case_id=CASE_ID, attempt=1, number_e164="+14155550142", customer_name="Northwind Traders",
                        callback_number="(415) 555-0100", vendor_names=(case.legal_name,), contact_point_id=PICK)
        if case.verified_setup or case.result not in STATUS_CALLS:
            call = CallResult(End(case.result, "lab", RETRY_AT if case.result == "callback_requested" else None),
                              _answers_object(case), dict(case.checks or {}), ())
            body = voice_app.report_body(call, CALL_ID, dial)
            if case.verified_setup:
                setup = {"direction": {"outbound": "outbound-api", "inbound": "inbound"}.get(case.direction or "", ""),
                         "customParameters": {"answered_by": case.answered_by or "unknown"}}
                voice_app.with_connect_facts(body, setup, call)
            return body
        result, answered_by = status_result(*STATUS_CALLS[case.result])    # the status callback (no connection)
        call = CallResult(End(result, "lab"), _answers_object(case), {}, ())
        body = voice_app.report_body(call, CALL_ID, dial)
        if answered_by is not None:
            body["answered_by"] = answered_by
        return body
    body = {"call_id": str(CALL_ID), "case_id": str(CASE_ID), "result": case.result, "attempt": 1,
            "contact_point_id": str(PICK)}
    if case.answers:
        body["answers"] = _answers_object(case)
    if case.result == "callback_requested":
        body["retry_at"] = RETRY_AT.strftime("%Y-%m-%dT%H:%M:%SZ")
    if case.checks:
        body["checks"] = dict(case.checks)
    for key in ("direction", "dialed_number_is_pick", "answered_by"):
        if getattr(case, key) is not None:
            body[key] = getattr(case, key)
    return body


def run(case: ReportCase, decide: Callable[[CaseFacts], Decision] | None = None) -> Ran:
    """A1's chain on one case, then `decide` (production's unless a double is given)."""
    body = body_of(case)
    CallEndedRequest.model_validate(body)                                   # the core's own check
    report = {k: v for k, v in body.items() if k not in ("case_id", "answers")}
    if "answers" in body:
        spoken = core_answers.spoken_last_four(body["answers"])
        report["last_four_result"] = dialback_decision.compare_last_four(spoken, case.new_last4, case.old_last4)
        report["answers"] = body["answers"]
    generation = [{"type": "number_resolved", "body": {"contact_point_id": str(PICK)}}]
    if not case.pick_current:
        generation.append({"type": "number_resolved", "body": {"contact_point_id": str(NEWER_PICK)}})
    generation += [{"type": "call_ended", "body": e} for e in case.earlier]
    context = ChangeFacts(speaker_in_change_thread=case.speaker_in_change_thread,
                          offered_number_from_request=case.offered_number_from_request)
    facts = core_facts.call_facts(report, {"legal_name": case.legal_name, "trade_names": list(case.trade_names)},
                                  generation, context)
    return Ran(body, report, facts, (decide or dialback_decision.decide)(facts))


EXPECTED_COVERAGE = {
    "consent": {"yes", "no"}, "change": {"yes", "no", "unsure"},
    "last_four": set(dialback_decision.LAST_FOUR_RESULTS), "company_truth": set(COMPANY_TRUTHS),
    "result": set(CALL_RESULTS), "answered_by": {"human", "machine", "unknown"},
    "direction": {"outbound", "inbound", "unknown"}, "flag": set(rules.FLAGS),
    "shape": {"missing", "twice", "out_of_order", "offered_number"},
    "case": {"trade_name", "earlier_call", "earlier_call_with_answers", "speaker_in_thread", "speaker_unknown",
             "offered_from_request", "stale_pick", "check_failing", "unverified_setup"},
}
_CONFIRMING = ("recording_consent", "company_named", "speaker_name", "speaker_role", "change_requested", "account_last4")


def coverage(cases: Iterable[ReportCase]) -> dict[str, set[str]]:
    """Which values, comparison results, truths and shapes a sample reaches (EXPECTED_COVERAGE's keys)."""
    seen: dict[str, set[str]] = {k: set() for k in EXPECTED_COVERAGE}
    for case in cases:
        ran = run(case)
        seen["consent"].update(case.values("recording_consent"))
        seen["change"].update(case.values("change_requested"))
        if "last_four_result" in ran.report:
            seen["last_four"].add(ran.report["last_four_result"])
        seen["company_truth"].update(case.truth["company"])
        seen["result"].add(case.result)
        seen["answered_by"].add(ran.body.get("answered_by", "unknown"))
        seen["direction"].add(ran.body.get("direction", "unknown"))
        seen["flag"].update(case.values("flag"))
        if case.result == "answers_recorded" and any(not case.values(s) for s in _CONFIRMING):
            seen["shape"].add("missing")
        if any(len(set(case.values(s))) > 1 for s in _CONFIRMING):
            seen["shape"].add("twice")
        if ran.facts.answers_in_order is False or ran.facts.consent_first is False:
            seen["shape"].add("out_of_order")
        if case.values("offered_number"):
            seen["shape"].add("offered_number")
        trades = {_norm(t) for t in case.trade_names}
        if any(_norm(p) in trades for p in case.values("company_named")):
            seen["case"].add("trade_name")
        if case.earlier:
            seen["case"].add("earlier_call_with_answers" if any("answers" in e for e in case.earlier) else "earlier_call")
        if case.speaker_in_change_thread is not False:
            seen["case"].add("speaker_in_thread" if case.speaker_in_change_thread else "speaker_unknown")
        if case.offered_number_from_request:
            seen["case"].add("offered_from_request")
        if not case.pick_current:
            seen["case"].add("stale_pick")
        if case.checks and any(case.checks[k] != v for k, v in CLEAN_CHECKS.items()):
            seen["case"].add("check_failing")
        if not case.verified_setup:
            seen["case"].add("unverified_setup")
    return seen


def honest(domain: str = "reachable", legal_name: str = "Acme Fasteners LLC") -> ReportCase:
    """One honest, full confirmation, the generator's starting point, built without Hypothesis (for tests)."""
    def answer(slot: str, value: str, turn: int, method: str = "speech") -> dict:
        return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"t{turn}_{slot}",
                "captured_at": f"2026-11-01T17:{turn:02d}:00Z", "capture_method": method}
    answers = (answer("recording_consent", "yes", 1), answer("company_named", "Acme Fasteners", 3),
               answer("department", "Accounts payable", 5), answer("speaker_name", "Jordan Lee", 7),
               answer("speaker_role", "AP clerk", 9), answer("change_requested", "yes", 11),
               answer("account_last4", "4821", 13, "keypad"))
    return ReportCase(domain=domain, result="answers_recorded", answers=answers, checks=dict(CLEAN_CHECKS),
                      verified_setup=True, direction="outbound", answered_by="human",
                      dialed_number_is_pick=True if domain == "contract" else None, pick_current=True,
                      new_last4="4821", old_last4="7730", legal_name=legal_name, trade_names=(), earlier=(),
                      speaker_in_change_thread=False, offered_number_from_request=False,
                      truth={"company": ["vendor"], "company_class": ["vendor"], "speaker_from_thread": False,
                             "offered_from_request": False, "earlier_concern": False})


def with_case(case: ReportCase, **changes) -> ReportCase:
    return dataclasses.replace(case, **changes)


def with_company(case: ReportCase, said: str, truth: str, cls: str | None = None) -> ReportCase:
    """The case with every company answer replaced by `said`, whose truth is `truth` (class `cls`)."""
    return _set_values(case, "company_named", said, truth, cls)


def recordable(case: ReportCase) -> bool:
    """Could production's call have recorded these answers (spec 0048 review fix 4)? Agents record only the
    current question's answer and questions run in the script's order, so the slots present are a prefix of
    it, each once, in rising turns; nothing is recorded without consent, and nothing after a refused one; the
    only flag is code's pressure flag, at or after consent; no production agent records an offered number."""
    slots = [a["slot"] for a in case.answers if a["slot"] != "flag"]
    present = [s for s in _ORDER if s in slots]
    if any(s not in _ORDER for s in slots) or len(slots) != len(set(slots)) or present != list(_ORDER[:len(present)]):
        return False
    turns = [next(a["turn_id"] for a in case.answers if a["slot"] == s) for s in present]
    if any(b <= a for a, b in itertools.pairwise(turns)):
        return False
    consent = case.values("recording_consent")
    if (not consent and case.answers) or (consent == ["no"] and len(case.answers) > 1):
        return False
    flags = [a for a in case.answers if a["slot"] == "flag"]
    return all(a["value"] == "pressure_to_approve" and a["turn_id"] >= turns[0] for a in flags)


def reachable(case: ReportCase) -> bool:
    return case.domain == "reachable" and recordable(case)


def report_cases(domain: str):
    """The Hypothesis strategy for one domain (imported here so `lab run` never needs Hypothesis)."""
    from .reportgen import report_cases as strategy
    return strategy(domain)


# ---- F1: verified only on an honest, full confirmation ----------------------------------------------

def unconfirmed(case: ReportCase) -> list[str]:
    """What an honest, full confirmation lacks, by the generator's truth, one condition per entry (spec 0048,
    B4 F1 and review fix 1). Empty when it is honest and full. It restates the verify conditions, so it
    cannot find a mistake in their design (FINDINGS B9); it finds a fact computed wrongly (A10 was one)."""
    missing: list[str] = []
    if case.result != "answers_recorded":
        missing.append(f"result={case.result}")
    if case.answered_by != "human":
        missing.append(f"answered_by={case.answered_by or 'unknown'}")
    if case.direction != "outbound":
        missing.append(f"direction={case.direction or 'unknown'}")
    if case.domain == "reachable" and not case.verified_setup:
        missing.append("setup=unverified")
    if not case.pick_current:
        missing.append("pick=stale")
    if case.domain == "contract" and case.dialed_number_is_pick is not True:
        missing.append(f"pick_reported={case.dialed_number_is_pick}")
    for slot, name in (("recording_consent", "consent"), ("change_requested", "change")):
        values = case.values(slot)
        if set(values) != {"yes"}:
            missing.append(f"{name}={'+'.join(sorted(set(values))) or 'missing'}")
    # Spec 0082, D7b (Sami, Nov 10): no refusal word in the company answer. Naming the vendor, or answering at
    # all, is no longer asked. Named by phrase class (review fix 5).
    missing += sorted({f"company={cls}" for said, cls in _company_answers(case) if refuses(cls, said)})
    digits = case.values("account_last4")
    if not digits:
        missing.append("digits=missing")
    elif len(set(digits)) > 1:
        missing.append("digits=two_values")
    elif digits[0] != case.new_last4:
        missing.append("digits=not_the_new_account")
    elif case.old_last4 == case.new_last4:
        missing.append("digits=accounts_end_alike")
    if case.checks is None or (case.domain == "reachable" and not case.verified_setup):
        missing.append("checks=missing")
    else:
        missing += [f"checks={k}" for k, v in CLEAN_CHECKS.items() if case.checks[k] != v]
    for key, name in (("speaker_from_thread", "speaker_from_thread"), ("offered_from_request", "offered_from_request"),
                      ("earlier_concern", "earlier_concern")):
        if case.truth[key]:
            missing.append(name)
    # Spec 0048 review fix 2: every verify condition, not only some.
    missing += [f"flag={flag}" for flag in sorted(set(case.values("flag")))]                    # V12
    if case.values("offered_number"):
        missing.append("offered_number")                                                   # V12
    names, roles = case.values("speaker_name"), [r for r in case.values("speaker_role") if r.strip()]
    if not names:
        missing.append("name=missing")                                                     # V06
    elif len(set(names)) > 1:
        missing.append("speaker=two_names")                                                # V09: one person
    if not roles:
        missing.append("role=missing")                                                     # V06
    elif len(set(roles)) > 1:
        missing.append("role=two_values")
    if not _in_order(case):
        missing.append("order=out_of_order")                                               # V04, V10
    return missing


_ORDER = ("recording_consent", "company_named", "department", "speaker_name", "speaker_role", "change_requested",
          "account_last4")


def _in_order(case: ReportCase) -> bool:
    """The questions' answers come in the script's order, each in a turn after the last, consent first: the
    first turn of each slot strictly rises along the order, and nothing is recorded at or before consent."""
    firsts = [min(a["turn_id"] for a in case.answers if a["slot"] == slot) for slot in _ORDER if case.values(slot)]
    if any(b <= a for a, b in itertools.pairwise(firsts)):
        return False
    consent = [a["turn_id"] for a in case.answers if a["slot"] == "recording_consent"]
    return not consent or all(a["turn_id"] > min(consent) for a in case.answers if a["slot"] != "recording_consent")


def honest_full(case: ReportCase) -> bool:
    return not unconfirmed(case)


def f1_label(condition: str) -> str:
    """F1's label names the condition that failed (spec 0048 review fix 1), e.g. "F1:company=negation"."""
    return f"F1:{condition}"


@dataclasses.dataclass
class Found:
    violations: list[tuple[int, list[str]]] = dataclasses.field(default_factory=list)   # (case index, labels)
    vendor_answers: int = 0                  # F3: answers whose truth names the vendor
    vendor_rejected: int = 0                 # F3: of those, the ones the check flags False (over-blocking, D7b)
    vendor_unknown: int = 0                  # F3: of those, the ones it can't read (None: blocks nothing)
    other_answers: int = 0                   # F3: answers naming someone else (spec 0082, D10)
    other_accepted: int = 0                  # F3: of those, the ones it reads as the vendor (True)
    other_unknown: int = 0                   # F3: of those, the ones it can't read (None: they pass too, D7b)
    per_class: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)   # F3: class -> reading -> count


def check_f1(cases: Iterable[ReportCase], decide=None) -> Found:
    found = Found()
    for i, case in enumerate(cases):
        if run(case, decide).decision.outcome == "verified" and (lacking := unconfirmed(case)):
            found.violations.append((i, [f1_label(c) for c in lacking]))
    return found


# ---- F2: less certainty never helps -----------------------------------------------------------------

_SLOT = {"consent": "recording_consent", "change": "change_requested", "digits": "account_last4",
         "company": "company_named", "name": "speaker_name", "role": "speaker_role"}
_SECOND = {"recording_consent": {"yes": "no", "no": "yes"}, "change_requested": {"yes": "no", "no": "yes", "unsure": "yes"}}
SECOND_TEXT = {"speaker_name": "Pat Second", "speaker_role": "Second role"}   # what the second_* moves add


def _with(case: ReportCase, **changes) -> ReportCase:
    return dataclasses.replace(case, **changes)


def company_classes(case: ReportCase) -> list[str]:
    """Each company answer's phrase class (spec 0048 review fix 5). Reports frozen before classes existed have
    only truths, and there each truth is its own class."""
    return list(case.truth.get("company_class", case.truth["company"]))


def _company_answers(case: ReportCase) -> list[tuple[str, str]]:
    """Each company answer as said, with its phrase class."""
    return list(zip(case.values("company_named"), company_classes(case), strict=True))


def _holds_refusal(case: ReportCase) -> bool:
    return any(refuses(cls, said) for said, cls in _company_answers(case))


def _companies(case: ReportCase, truths: list[str], classes: list[str]) -> dict:
    return {**case.truth, "company": truths, "company_class": classes}


def _set_values(case: ReportCase, slot: str, value: str, truth: str | None = None,
                cls: str | None = None) -> ReportCase | None:
    if not case.values(slot):
        return None
    answers = tuple({**a, "value": value} if a["slot"] == slot else a for a in case.answers)
    n = len(case.truth["company"])
    truth_after = _companies(case, [truth] * n, [cls or truth] * n) if slot == "company_named" else case.truth
    return _with(case, answers=answers, truth=truth_after)


def _near_miss(digits: str) -> str:
    return digits[:-1] + str((int(digits[-1]) + 1) % 10)


def _second(case: ReportCase, slot: str) -> ReportCase | None:
    values = case.values(slot)
    if not values:
        return None
    first = values[0]
    if slot in _SECOND:
        other = _SECOND[slot][first]
    elif slot == "account_last4":
        other = _near_miss(case.new_last4) if first != _near_miss(case.new_last4) else "0000"
    elif slot == "company_named":
        other = "Globex Logistics"
    else:
        other = SECOND_TEXT[slot]
    turn = max(a["turn_id"] for a in case.answers) + 2
    extra = {"slot": slot, "value": other, "turn_id": turn, "tool_call_id": f"second_{turn}",
             "captured_at": "2026-11-01T17:05:00Z", "capture_method": "speech"}
    truth = (_companies(case, [*case.truth["company"], "other"], [*company_classes(case), "other"])
             if slot == "company_named" else case.truth)
    return _with(case, answers=(*case.answers, extra), truth=truth)


def _drop(case: ReportCase, slot: str) -> ReportCase | None:
    if not case.values(slot):
        return None
    truth = _companies(case, [], []) if slot == "company_named" else case.truth
    return _with(case, answers=tuple(a for a in case.answers if a["slot"] != slot), truth=truth)


def _earlier_concern(case: ReportCase) -> ReportCase | None:
    concern = {"call_id": f"00000000-0000-0000-0000-0000000{len(case.earlier) + 1:05d}", "result": "callback_requested",
               "answers": {"schema_version": "call-answers/v0", "call_id": "x", "answers": [
                   {"slot": "recording_consent", "value": "yes", "turn_id": 1, "tool_call_id": "e1",
                    "captured_at": "2026-10-31T17:00:00Z", "capture_method": "speech"},
                   {"slot": "change_requested", "value": "unsure", "turn_id": 3, "tool_call_id": "e3",
                    "captured_at": "2026-10-31T17:01:00Z", "capture_method": "speech"}]}}
    return _with(case, domain="contract", earlier=(*case.earlier, concern), truth={**case.truth, "earlier_concern": True})


def _check_failing(case: ReportCase, name: str) -> ReportCase | None:
    if case.checks is None or case.checks[name] == FAILING_CHECKS[name]:
        return None
    return _with(case, checks={**case.checks, name: FAILING_CHECKS[name]})


def _company(truth: str) -> Callable[[ReportCase], ReportCase | None]:
    def move(case: ReportCase) -> ReportCase | None:
        from .phrasebank import phrase
        if truth not in REFUSAL_CLASSES and _holds_refusal(case):
            return None             # replacing a refusal drops evidence against the change (see RETIRED_MOVES)
        return _set_values(case, "company_named", phrase(truth, case.legal_name, case.trade_names), truth)
    return move


# Spec 0082 (Sami, Nov 10) changes three of spec 0048 B4's company moves:
# - company_other stays, but never replaces a refusal: since D7b a refusal is the company answer's only evidence
#   against the change (NR03), and dropping evidence against may help, as dropping a flag may (B4).
# - company_partial and drop_company are retired. A short name is a match (D6), and the oracle expects a legit
#   caller who gives one to verify, so it is no less certain than the full name. The company answer is no longer
#   one of the confirming answers (D7b), so dropping it is not dropping one; it helps only where that answer was
#   evidence against (a refusal, or an answer recorded before consent, NR02), which B4 lets help.
RETIRED_MOVES = {"company_partial": "a short name is a match (spec 0082, D6)",
                 "drop_company": "the company answer is no longer a confirming answer (spec 0082, D7b)"}


MOVES: dict[str, Callable[[ReportCase], ReportCase | None]] = {
    "change_unsure": lambda c: _set_values(c, "change_requested", "unsure"),
    "change_no": lambda c: _set_values(c, "change_requested", "no"),
    "digits_dont_know": lambda c: _set_values(c, "account_last4", "dont_know"),
    "digits_refused": lambda c: _set_values(c, "account_last4", "refused"),
    "digits_near_miss": lambda c: _set_values(c, "account_last4", _near_miss(c.new_last4)),
    "consent_no": lambda c: _set_values(c, "recording_consent", "no"),
    "company_other": _company("other"),
    "company_negation": _company("negation"),
    "answerer_machine": lambda c: _with(c, answered_by="machine") if c.answered_by != "machine" else None,
    "answerer_unknown": lambda c: _with(c, answered_by=None) if c.answered_by is not None else None,
    **{f"second_{k}": (lambda c, s=s: _second(c, s)) for k, s in _SLOT.items()},
    **{f"drop_{k}": (lambda c, s=s: _drop(c, s)) for k, s in _SLOT.items() if f"drop_{k}" not in RETIRED_MOVES},
    "earlier_concern": _earlier_concern,
    **{f"check_fails_{k}": (lambda c, k=k: _check_failing(c, k)) for k in CLEAN_CHECKS},
    "speaker_from_thread": lambda c: (_with(c, speaker_in_change_thread=True,
                                            truth={**c.truth, "speaker_from_thread": True})
                                      if c.speaker_in_change_thread is not True else None),
}
CONTRACT_MOVES = frozenset({"earlier_concern"})   # earlier calls with answers: contract only (A16)


def f2_label(move: str) -> str:
    return f"F2_{move}_helped"


def check_f2(cases: Iterable[ReportCase], decide=None, moves: Iterable[str] = tuple(MOVES)) -> Found:
    found = Found()
    moves = tuple(moves)
    for i, case in enumerate(cases):
        if run(case, decide).decision.outcome == "verified":
            continue
        labels = []
        for name in moves:
            if name in CONTRACT_MOVES and case.domain != "contract":
                continue
            moved = MOVES[name](case)
            if moved is not None and case.domain == "reachable" and not reachable(moved):
                continue            # a move production could not record stays in the contract-only domain
            if moved is not None and run(moved, decide).decision.outcome == "verified":
                labels.append(f2_label(name))
        if labels:
            found.violations.append((i, labels))
    return found


# ---- F3: the company check against the truth --------------------------------------------------------

def f3_label(cls: str) -> str:
    """F3's label names the refusal class the company check let through (spec 0082, D7b)."""
    return f"F3_company_refusal_missed_{cls}"


_READING = {True: "true", None: "unknown", False: "false"}


def check_f3(cases: Iterable[ReportCase], matcher=every_name_matches) -> Found:
    """Each company answer alone against the vendor's names (Sami, Nov 10; spec 0082, D7b): an answer whose
    phrase class refuses (negation, reseller, past connection) must be False, since only False flags it (gated).
    Every other class is reported, not gated: each class's True, unknown and False counts are kept in
    `per_class` (spec 0048 review fix 5), with over-blocking (names-the-vendor answers that come out False) and
    other-company acceptance (answers naming someone else that come out True; spec 0082, D10)."""
    found = Found()
    for i, case in enumerate(cases):
        labels = []
        for phrase, truth, cls in zip(case.values("company_named"), case.truth["company"], company_classes(case),
                                      strict=True):
            reading = matcher([phrase], case.legal_name, case.trade_names)
            counts = found.per_class.setdefault(cls, {"true": 0, "unknown": 0, "false": 0})
            counts[_READING[reading]] += 1
            if cls in REFUSAL_CLASSES and reading is not False:
                labels.append(f3_label(cls))
            if truth == "vendor":
                found.vendor_answers += 1
                found.vendor_rejected += reading is False
                found.vendor_unknown += reading is None
            elif truth in NAMES_SOMEONE_ELSE:
                found.other_answers += 1
                found.other_accepted += reading is True
                found.other_unknown += reading is None
        if labels:
            found.violations.append((i, sorted(set(labels))))
    return found


_SUFFIXES = frozenset({"llc", "inc", "incorporated", "co", "corp", "corporation", "ltd", "limited", "company", "plc",
                       "lp", "llp", "the"})


def _norm(name: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", name.lower().replace("&", " and ")) if w not in _SUFFIXES)


def strict_match(named: Iterable[str], legal_name: str, trade_names: Iterable[str] = ()) -> bool | None:
    """The strict reference matcher: every answer is exactly the vendor's legal name or a trade name, apart
    from case, punctuation and a legal suffix. It rejects filler words too; it exists to show the phrase
    bank's truth labels are sound, not to be fair to real speech."""
    names = {_norm(n) for n in (legal_name, *trade_names)}
    said = [_norm(n) for n in named]
    return bool(said) and all(s in names for s in said)


# ---- the broken doubles -----------------------------------------------------------------------------

@contextmanager
def _table(skip: Iterable[str] = (), replace: dict[str, Callable] | None = None):
    """The rule table with some rules left out and some tests replaced, for the length of the block."""
    skip, replace = set(skip), replace or {}
    saved = {block: getattr(rules, block) for block in ("BLOCK0", "NOT_VERIFIED", "NEEDS_REVIEW", "VERIFIED")}
    try:
        for block, table in saved.items():
            setattr(rules, block, tuple(dataclasses.replace(r, test=replace[r.id]) if r.id in replace else r
                                        for r in table if r.id not in skip))
        yield
    finally:
        for block, table in saved.items():
            setattr(rules, block, table)


def _accepting_unsure(f: CaseFacts, m) -> bool:
    return bool(f.change_requested) and set(f.change_requested) <= {"yes", "unsure"} \
        and f.speaker_in_change_thread is False


def _clean(c) -> bool:
    return c is not None and all(getattr(c, k) == v for k, v in CLEAN_CHECKS.items())


def _phrases_all(facts: CaseFacts, truth: str) -> bool:
    from .phrasebank import truths
    return bool(facts.company_named) and all(truth in truths(c) for c in facts.company_named)


def _earlier_unsure(facts: CaseFacts) -> bool:
    return any("unsure" in c.change_requested for c in facts.earlier_calls)


# move -> (the rules that read the moved answer, and when the facts carry exactly the move's value). Each
# double lets only its own move's value through, so a base that is wrong some other way about the same
# answer stays refused and the move is what flips it (e.g. digits that mismatch, then dont_know).
_LETS_THROUGH: dict[str, tuple[str, Callable[[CaseFacts], bool]]] = {
    "change_unsure": ("change_requested", lambda f: "unsure" in f.change_requested),
    "change_no": ("change_requested", lambda f: "no" in f.change_requested),
    "second_change": ("change_requested", lambda f: len(set(f.change_requested)) > 1),
    "drop_change": ("change_requested", lambda f: not f.change_requested),
    "digits_dont_know": ("account_last4", lambda f: f.last_four_result == "not_provided"),
    "digits_refused": ("account_last4", lambda f: f.last_four_result == "not_provided"),
    "digits_near_miss": ("account_last4", lambda f: f.last_four_result == "near_miss"),
    "second_digits": ("account_last4", lambda f: f.last_four_result == "inconsistent"),
    "drop_digits": ("account_last4", lambda f: f.last_four_result == "not_provided"),
    "consent_no": ("recording_consent", lambda f: "no" in f.recording_consent),
    "second_consent": ("recording_consent", lambda f: len(set(f.recording_consent)) > 1),
    "drop_consent": ("recording_consent", lambda f: not f.recording_consent),
    "company_other": ("company_named", lambda f: _phrases_all(f, "other")),
    "company_negation": ("company_named", lambda f: _phrases_all(f, "negation")),
    "second_company": ("company_named", lambda f: len(set(f.company_named)) > 1),
    "second_name": ("speaker_name", lambda f: f.same_person_yes_and_digits is False),
    "drop_name": ("speaker_name", lambda f: not f.speaker_named),
    "second_role": ("speaker_role", lambda f: SECOND_TEXT["speaker_role"] in f.speaker_role),
    "drop_role": ("speaker_role", lambda f: not f.speaker_role),
    "answerer_machine": ("answered_by", lambda f: f.answered_by == "machine"),
    "answerer_unknown": ("answered_by", lambda f: f.answered_by is None),
    "earlier_concern": ("earlier", _earlier_unsure),
    **{f"check_fails_{k}": ("checks", lambda f, k=k: f.checks is not None and getattr(f.checks, k) == FAILING_CHECKS[k])
       for k in CLEAN_CHECKS},
    "speaker_from_thread": ("speaker", lambda f: f.speaker_in_change_thread is True),
}
_RULES_READING = {**rules.SLOT_RULES, "answered_by": ("NR01_no_live_person", "V03_live_person"),
                  "earlier": ("NR19_earlier_call_concern",),
                  "checks": ("NR14_answer_not_in_transcript", "NR15_script_violation", "V10_answers_grounded",
                             "V11_script_clean"),
                  "speaker": ("NR04_speaker_from_change_thread", "V07_right_person_confirmed")}


class doubles:  # a namespace of broken stand-ins, each the double its check must fail on
    @staticmethod
    def v07_accepts_unsure_nr09_off(facts: CaseFacts) -> Decision:
        """F1's double (spec 0048, B5): a decide() whose V07 accepts "unsure" and whose NR09 is turned off.
        NR09 refuses any unsure answer first, so the V07 change alone would change nothing."""
        with _table(skip=("NR09_change_unsure",), replace={"V07_right_person_confirmed": _accepting_unsure}):
            return dialback_decision.decide(facts)

    @staticmethod
    def lets_through(move: str) -> Callable[[CaseFacts], Decision]:
        """F2's double for one move: the rules that read the moved answer are left out whenever the facts
        carry the move's less certain value (e.g. the digit rules when the digits are dont_know)."""
        reads, carries = _LETS_THROUGH[move]

        def decide(facts: CaseFacts) -> Decision:
            if not carries(facts):
                return dialback_decision.decide(facts)
            with _table(skip=_RULES_READING[reads]):
                return dialback_decision.decide(facts)
        return decide

    @staticmethod
    def nr03_fires_on_a_match(facts: CaseFacts) -> Decision:
        """F2's double for company_other, which no rule reads since spec 0082 D7b (another company is unknown, and
        NR03 reads only refusals), so no double can let it through: NR03 also fires on a match, as a company check
        that read the vendor's own name as a refusal would. Replacing that answer with another company verifies."""
        with _table(replace={"NR03_wrong_company": lambda f, m: f.company_matches is not None}):
            return dialback_decision.decide(facts)

    @staticmethod
    def contains_vendor_name(named: Iterable[str], legal_name: str, trade_names: Iterable[str] = ()) -> bool | None:
        """F3's double: any answer containing the vendor's name (or a trade name) is a match."""
        said = [f" {_norm(n)} " for n in named]
        names = [f" {_norm(n)} " for n in (legal_name, *trade_names)]
        return bool(said) and all(any(name in s for name in names) for s in said)


# ---- running all three, freezing, re-running --------------------------------------------------------

def labels(case: ReportCase, decide=None, matcher=every_name_matches) -> list[str]:
    """Every check's labels on one case (F2 over every move that applies to its domain)."""
    found = [*(lab for _, labs in check_f1([case], decide).violations for lab in labs),
             *(lab for _, labs in check_f2([case], decide).violations for lab in labs),
             *(lab for _, labs in check_f3([case], matcher).violations for lab in labs)]
    return sorted(set(found))


FILE = "report.json"


@dataclasses.dataclass(frozen=True)
class Frozen:
    name: str
    labels: tuple[str, ...]
    case: ReportCase


def case_digest(case: ReportCase) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(case), sort_keys=True).encode()).hexdigest()


def _load_case(data: dict) -> ReportCase:
    data = dict(data)
    for key in ("answers", "trade_names", "earlier"):
        data[key] = tuple(data[key])
    return ReportCase(**data)


def freeze(case: ReportCase, found: list[str], out_dir: Path, why: str, domain: str | None = None) -> Path:
    """Write the case under `<out_dir>/<name>/report.json`. A frozen case is never replaced, and its labels
    record what failed when it was frozen (as the fuzz lab's frozen worlds)."""
    name = f"reports-{re.sub(r'[^a-z0-9]+', '-', found[0].lower()).strip('-')}-{case_digest(case)[:8]}"
    folder = Path(out_dir) / name
    if (folder / FILE).is_file():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    body = {"name": name, "labels": found, "why": why, "case": dataclasses.asdict(case)}
    (folder / FILE).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return folder


def load_all(out_dir: Path) -> list[Frozen]:
    root = Path(out_dir)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir()):
        if (folder / FILE).is_file():
            data = json.loads((folder / FILE).read_text(encoding="utf-8"))
            out.append(Frozen(data["name"], tuple(data["labels"]), _load_case(data["case"])))
    return out


def rerun(out_dir: Path, decide=None, matcher=every_name_matches) -> list[tuple[Frozen, list[str]]]:
    """Every frozen case with the labels it shows now."""
    return [(frozen, labels(frozen.case, decide, matcher)) for frozen in load_all(out_dir)]


def _honest_step(case: ReportCase) -> list[ReportCase]:
    """Smaller cases to try while shrinking: one answer fewer, or one field back to its plain value."""
    out = [dataclasses.replace(case, answers=case.answers[:i] + case.answers[i + 1:],
                               truth=_companies(case, [t for j, t in enumerate(case.truth["company"])
                                                       if j != _company_index(case, i)],
                                                [c for j, c in enumerate(company_classes(case))
                                                 if j != _company_index(case, i)]))
           for i in range(len(case.answers))]
    out += [dataclasses.replace(case, earlier=case.earlier[:i] + case.earlier[i + 1:]) for i in range(len(case.earlier))]
    return out


def _company_index(case: ReportCase, answer_index: int) -> int | None:
    if case.answers[answer_index]["slot"] != "company_named":
        return None
    return sum(1 for a in case.answers[:answer_index] if a["slot"] == "company_named")


def shrink(case: ReportCase, label: str, decide=None, matcher=every_name_matches) -> ReportCase:
    """Greedy: drop answers and earlier calls one at a time while the case still shows `label`."""
    changed = True
    while changed:
        changed = False
        for smaller in _honest_step(case):
            if case.domain == "reachable" and not recordable(smaller):
                continue                             # a reachable finding stays one production could send
            try:
                if label in labels(smaller, decide, matcher):
                    case, changed = smaller, True
                    break
            except ValueError:                       # a smaller report the core would refuse is not a case
                continue
    return case


@dataclasses.dataclass
class Checked:
    examples: int
    seed: int
    hits: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)   # domain -> label -> count
    first: dict[tuple[str, str], ReportCase] = dataclasses.field(default_factory=dict)   # (domain, label) -> case
    frozen_failing: list[tuple[str, list[str]]] = dataclasses.field(default_factory=list)
    new_frozen: list[str] = dataclasses.field(default_factory=list)
    vendor_answers: int = 0
    vendor_rejected: int = 0
    vendor_unknown: int = 0
    other_answers: int = 0
    other_accepted: int = 0
    other_unknown: int = 0
    reachable_hits: list[list[str]] = dataclasses.field(default_factory=list)   # each violating report's labels
    per_class: dict[str, dict[str, dict[str, int]]] = dataclasses.field(default_factory=dict)   # domain -> class -> counts

    @property
    def gated(self) -> list[str]:
        """Labels seen on the reachable domain, which gate the job (frozen re-runs are reported on their own)."""
        return sorted(self.hits.get("reachable", {}))

    @property
    def future_state(self) -> list[str]:
        """Labels seen only in the contract-only domain: findings for the owner, not gated."""
        return sorted(set(self.hits.get("contract", {})) - set(self.hits.get("reachable", {})))

    def summary(self) -> str:
        share = self.vendor_rejected / self.vendor_answers if self.vendor_answers else 0.0
        return (f"reachable {self.hits.get('reachable', {})}; contract-only future-state {self.future_state}; "
                f"frozen still failing {len(self.frozen_failing)}; over-blocking {self.vendor_rejected} of "
                f"{self.vendor_answers} names-the-vendor answers flagged False ({share:.1%})")


def run_checks(*, examples: int, seed: int, out_dir: Path | None = None, decide=None,
               matcher=every_name_matches) -> Checked:
    """F1 to F3 over `examples` pinned reports per domain at `seed`, every report evaluated (the search never
    stops at a hit). With `out_dir`, the frozen reports there are re-run first, and the first report showing
    each label not frozen yet is shrunk and frozen."""
    from .draws import sample
    done = Checked(examples, seed)
    if out_dir is not None:
        done.frozen_failing = [(f.name, now) for f, now in rerun(out_dir, decide, matcher) if now]
    for domain in ("reachable", "contract"):
        cases = sample(report_cases(domain), examples=examples, seed=seed)
        f3 = check_f3(cases, matcher)
        for count in ("vendor_answers", "vendor_rejected", "vendor_unknown", "other_answers", "other_accepted",
                      "other_unknown"):
            setattr(done, count, getattr(done, count) + getattr(f3, count))
        done.per_class[domain] = f3.per_class
        per_report: dict[int, set[str]] = {}
        for found in (check_f1(cases, decide), check_f2(cases, decide), f3):
            for i, labs in found.violations:
                per_report.setdefault(i, set()).update(labs)
                for label in labs:
                    done.hits.setdefault(domain, {}).setdefault(label, 0)
                    done.hits[domain][label] += 1
                    done.first.setdefault((domain, label), cases[i])
        if domain == "reachable":
            done.reachable_hits = [sorted(labs) for _, labs in sorted(per_report.items())]
    if out_dir is not None:      # known: what the frozen cases show now (a label can be renamed, a case can go stale)
        known = {label for _, now in rerun(out_dir, decide, matcher) for label in now}
        order = {"reachable": 0, "contract": 1}          # a finding is frozen from the gated domain when it shows there
        for (domain, label), case in sorted(done.first.items(), key=lambda item: (order[item[0][0]], item[0][1])):
            if label in known:
                continue
            small = shrink(case, label, decide, matcher)
            shown = labels(small, decide, matcher) or [label]
            freeze(small, shown, out_dir, domain=domain,
                   why=f"found by lab reports at seed {seed} within {examples} reports ({domain} domain), shrunk")
            known |= set(shown)
            done.new_frozen.append(label)
    return done
