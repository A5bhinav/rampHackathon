"""Spec 0048, A1 (Q102): call_facts() is everything Worker.decide does after
its database reads, pulled out so the attack lab can chain it. Each fact is pinned here, and the worker,
run with its reads faked (no database), decides exactly what it decided before the extraction."""
from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from dialback_core.call_facts import call_facts
from dialback_core.company import every_name_matches
from dialback_core.ports import ChangeFacts
from dialback_core.worker import Command, Worker
from dialback_decision import CallChecks, decide
from dialback_decision.facts import earlier_call, facts_from_answers, values

CALL, OTHER_CALL = str(uuid.UUID(int=21)), str(uuid.UUID(int=22))
PICK, OLD_PICK = str(uuid.UUID(int=31)), str(uuid.UUID(int=32))
CASE = {"id": uuid.UUID(int=41), "dataset_id": uuid.UUID(int=42), "vendor_id": uuid.UUID(int=43), "state": "deciding",
        "version": 3, "legal_name": "Acme Fasteners LLC", "trade_names": ["Acme Supply"], "ramp_changed_at": None,
        "detected_at": None}
CHECKS = {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
          "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}


def _answer(slot, value, turn):
    return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"t{turn}",
            "captured_at": "2026-11-01T17:00:00Z", "capture_method": "speech"}


ANSWERS = [_answer("recording_consent", "yes", 1), _answer("company_named", "Acme Supply", 3),
           _answer("speaker_name", "Jordan Lee", 7), _answer("speaker_role", "AP clerk", 9),
           _answer("change_requested", "yes", 11), _answer("flag", "pressure_to_approve", 11)]


def _report(**over):
    body = {"call_id": CALL, "result": "answers_recorded", "attempt": 1, "contact_point_id": PICK,
            "answers": {"schema_version": "call-answers/v0", "call_id": CALL, "answers": ANSWERS},
            "checks": CHECKS, "direction": "outbound", "dialed_number_is_pick": True, "answered_by": "human",
            "last_four_result": "match"}
    return {k: v for k, v in (body | over).items() if v is not None}


GENERATION = [{"type": "number_resolved", "body": {"contact_point_id": OLD_PICK}},
              {"type": "call_ended", "body": {"call_id": OTHER_CALL, "result": "callback_requested"}},
              {"type": "number_resolved", "body": {"contact_point_id": PICK}},
              {"type": "call_ended", "body": {"call_id": CALL, "result": "answers_recorded"}}]
CONTEXT = ChangeFacts(speaker_in_change_thread=False, offered_number_from_request=None,
                      speaker_known_before_request=True)


def test_the_pick_is_this_versions_latest():
    assert call_facts(_report(), CASE, GENERATION, CONTEXT).dialed_eligible_number is True
    assert call_facts(_report(contact_point_id=OLD_PICK), CASE, GENERATION, CONTEXT).dialed_eligible_number is False
    assert call_facts(_report(contact_point_id=None), CASE, GENERATION, CONTEXT).dialed_eligible_number is False
    assert call_facts(_report(), CASE, GENERATION[1:2], CONTEXT).dialed_eligible_number is False   # no pick at all


def test_earlier_calls_are_the_others_of_this_generation():
    facts = call_facts(_report(), CASE, GENERATION, CONTEXT)
    assert [c.result for c in facts.earlier_calls] == ["callback_requested"]


def test_the_company_matches_a_trade_name():
    assert call_facts(_report(), CASE, GENERATION, CONTEXT).company_matches is True

    def said(company):
        answers = [*ANSWERS[:1], _answer("company_named", company, 3), *ANSWERS[2:]]
        return call_facts(_report(answers={"schema_version": "call-answers/v0", "call_id": CALL, "answers": answers}),
                          CASE, GENERATION, CONTEXT).company_matches
    assert said("Globex Logistics") is None, "spec 0082, D7b: another company is unknown, not a refusal"
    assert said("Not Acme Supply, this is Globex") is False, "a refusal word is False (NR03)"


def test_typed_digits_survive_the_stored_shape():  # spec 0080, Part B (review fix)
    # The worker decides from the stored body, whose digits `protect` has hashed. The capture method must
    # survive that, so an unlabeled call with typed digits verifies and one with spoken digits does not.
    from dialback_core.answers import protect
    clean = [a for a in ANSWERS if a["slot"] != "flag"]
    key = b"k" * 32

    def stored(method):
        digits = {**_answer("account_last4", "4821", 13), "capture_method": method}
        body = protect({"schema_version": "call-answers/v0", "call_id": CALL, "answers": [*clean, digits]}, key, 1)
        assert body["answers"][-1]["value"] != "4821"        # hashed, as the core stores it
        return call_facts(_report(answers=body, answered_by=None), CASE, GENERATION, CONTEXT)

    typed = stored("keypad")
    assert (typed.answered_by, typed.digits_typed) == (None, True) and decide(typed).outcome == "verified"
    spoken = stored("speech")
    assert spoken.digits_typed is False and decide(spoken).outcome == "needs_review"


def test_the_report_and_the_context_carry_through():
    facts = call_facts(_report(), CASE, GENERATION, CONTEXT)
    assert (facts.call_result, facts.last_four_result, facts.dialed_out, facts.dialed_number_is_pick,
            facts.answered_by) == ("answers_recorded", "match", True, True, "human")
    assert facts.checks == CallChecks(**CHECKS)
    assert (facts.speaker_in_change_thread, facts.offered_number_from_request, facts.speaker_known_before_request) == (
        False, None, True)
    assert facts.recording_consent == ("yes",) and facts.flags == ("pressure_to_approve",)
    assert facts.consent_first is True and facts.speaker_named is True
    bare = call_facts(_report(direction=None, checks=None, answers=None, result="no_answer"), CASE, GENERATION,
                      CONTEXT)
    assert (bare.dialed_out, bare.checks, bare.recording_consent) == (None, None, ())
    assert call_facts(_report(direction="inbound"), CASE, GENERATION, CONTEXT).dialed_out is False


# ---- the worker decides as it did before the extraction -------------------------------------------

def _before(body, case, generation, context):
    """Worker.decide's fact assembly as it stood before spec 0048 (e00dcf1), kept as the reference."""
    answers = (body.get("answers") or {}).get("answers") or []
    picks = [e["body"].get("contact_point_id") for e in generation if e["type"] == "number_resolved"]
    dialed_eligible = bool(picks) and body.get("contact_point_id") == picks[-1]
    earlier = tuple(earlier_call(e["body"]) for e in generation
                    if e["type"] == "call_ended" and e["body"].get("call_id") != body.get("call_id"))
    checks = body.get("checks")
    return facts_from_answers(
        answers, call_result=body["result"], dialed_eligible_number=dialed_eligible,
        last_four_result=body.get("last_four_result"),
        company_matches=every_name_matches(values(answers, "company_named"), case["legal_name"], case["trade_names"] or ()),
        checks=CallChecks(**checks) if checks else None, earlier_calls=earlier,
        dialed_out=None if body.get("direction") is None else body["direction"] == "outbound",
        dialed_number_is_pick=body.get("dialed_number_is_pick"), answered_by=body.get("answered_by"),
        speaker_in_change_thread=context.speaker_in_change_thread,
        offered_number_from_request=context.offered_number_from_request,
        speaker_known_before_request=context.speaker_known_before_request)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql, params=None):
        return _Rows(self.rows)

    def transaction(self):
        return contextlib.nullcontext()


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    @contextlib.asynccontextmanager
    async def connection(self):
        yield _Conn(self.rows)


class _Context:
    def __init__(self, facts):
        self.facts, self.asked = facts, None

    async def check(self, **kwargs):
        self.asked = kwargs
        return self.facts


class _AsyncNull(contextlib.nullcontext):
    async def __aenter__(self):
        return self.enter_result

    async def __aexit__(self, *exc):
        return False


REPORTS = [_report(), _report(contact_point_id=OLD_PICK), _report(direction="inbound", answered_by=None),
           _report(checks=None, last_four_result="near_miss"), _report(result="no_answer", answers=None),
           _report(answers={"schema_version": "call-answers/v0", "call_id": CALL,
                            "answers": [*ANSWERS, _answer("change_requested", "no", 12)]})]


@pytest.mark.parametrize("body", REPORTS, ids=[f"report-{i}" for i in range(len(REPORTS))])
def test_the_worker_decides_as_before(body, monkeypatch):
    worker = Worker.__new__(Worker)                                # the decide path only; nothing else is set up
    worker.pool, worker.change_context = _Pool(GENERATION), _Context(CONTEXT)
    emitted = []

    async def case(case_id):
        return CASE

    async def emit(case, event, key, payload):
        emitted.append((event, payload))
    worker._case, worker._emit = case, emit
    monkeypatch.setattr("dialback_core.worker.system_tx", lambda pool: _AsyncNull(_Conn(GENERATION)))
    cmd = Command(1, CASE["dataset_id"], CASE["id"], "decide", "k", body | {"version": 3}, 0, 5)
    asyncio.run(worker.decide(cmd))
    assert emitted == [("decision_made", decide(_before(body, CASE, GENERATION, CONTEXT)).body())]
    assert worker.change_context.asked["speaker_names"] == values((body.get("answers") or {}).get("answers") or [],
                                                                  "speaker_name")
