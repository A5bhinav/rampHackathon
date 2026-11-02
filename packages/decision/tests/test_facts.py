"""The facts decide() reads (spec 0005): repeated answers keep every value, the call order
and the single speaker are computed from the answers' turns, earlier calls are summarized,
and anything without a source is None."""
from dialback_decision import CaseFacts
from dialback_decision.engine import inputs_hash
from dialback_decision.facts import (
    EarlierCall,
    answers_in_order,
    consent_first,
    earlier_call,
    facts_from_answers,
    same_person,
)


def a(slot, value, turn):
    return {"slot": slot, "value": value, "turn_id": turn}


CALL = [a("recording_consent", "yes", 1), a("company_named", "Acme Fasteners", 3), a("speaker_name", "Jordan Lee", 5),
        a("speaker_role", "AR manager", 5), a("change_requested", "yes", 7), a("account_last4", "6666", 9)]


def test_consent_must_be_the_first_answer():
    assert consent_first(CALL) is True
    greeting = [a("speaker_name", "Jordan Lee", 0), *CALL]  # a voicemail greeting before consent
    assert consent_first(greeting) is False
    assert consent_first([a("company_named", "Acme", 3)]) is False, "no consent at all"
    assert consent_first([a("recording_consent", "yes", 1), a("company_named", "Acme", 1)]) is False, "a shared first turn"
    assert consent_first([]) is None


def test_answers_must_come_in_the_script_order():
    assert answers_in_order(CALL) is True
    one_breath = [a("recording_consent", "yes", 1), a("speaker_name", "Jordan Lee", 4), a("change_requested", "yes", 4),
                  a("account_last4", "6666", 6)]
    assert answers_in_order(one_breath) is True, "'Jordan here, and yes, we asked' is in order"
    name_late = [a("recording_consent", "yes", 1), a("change_requested", "yes", 4), a("speaker_name", "Jordan Lee", 6)]
    assert answers_in_order(name_late) is False
    digits_first = [a("recording_consent", "yes", 1), a("account_last4", "6666", 3), a("change_requested", "yes", 5)]
    assert answers_in_order(digits_first) is False
    same_yes = [a("recording_consent", "yes", 1), a("change_requested", "yes", 1)]
    assert answers_in_order(same_yes) is False, "one 'yes' can't answer both consent and the change"
    assert answers_in_order([a("recording_consent", "yes", 1)]) is None, "nothing to order around"


def test_one_declared_speaker_gives_both_the_yes_and_the_digits():
    assert same_person(CALL) is True
    assert same_person([*CALL, a("speaker_name", "jordan  lee.", 8)]) is True, "case, spacing and punctuation don't count"
    handoff = [*CALL[:4], a("speaker_name", "Sam Ortiz", 6), *CALL[4:]]
    assert same_person(handoff) is False
    assert same_person([*CALL, a("speaker_name", "Jordan", 8)]) is False, "a shorter name is another name: fail closed"
    name_late = [a("recording_consent", "yes", 1), a("change_requested", "yes", 4), a("speaker_name", "Jordan Lee", 6)]
    assert same_person(name_late) is None
    assert same_person([a("recording_consent", "yes", 1), a("change_requested", "yes", 4)]) is None, "no name"
    assert same_person([a("speaker_name", "Jordan Lee", 2)]) is None, "no change answer to tie the name to"


def test_repeated_answers_keep_every_value_in_order():
    answers = [*CALL, a("change_requested", "no", 11), a("company_named", "Globex", 12), a("flag", "pressure_to_approve", 13)]
    f = facts_from_answers(answers, call_result="answers_recorded", dialed_eligible_number=True)
    assert f.change_requested == ("yes", "no") and f.company_named == ("Acme Fasteners", "Globex")
    assert f.recording_consent == ("yes",) and f.speaker_role == ("AR manager",) and f.speaker_named
    assert f.flags == ("pressure_to_approve",) and not f.offered_number
    assert (f.consent_first, f.answers_in_order, f.same_person_yes_and_digits) == (True, True, True)


def test_missing_sources_are_none():
    f = facts_from_answers([], call_result="answers_recorded", dialed_eligible_number=False)
    unknown = ("consent_first", "answers_in_order", "same_person_yes_and_digits", "dialed_out", "dialed_number_is_pick",
               "answered_by", "line_risk", "speaker_in_change_thread", "offered_number_from_request",
               "speaker_known_before_request", "company_matches", "checks", "last_four_result")
    assert all(getattr(f, name) is None for name in unknown), {n: getattr(f, n) for n in unknown}
    assert f.earlier_calls == () and f.change_requested == () and not f.speaker_named


def test_an_earlier_call_is_summarized_from_its_stored_body():
    body = {"call_id": "c1", "result": "failed", "attempt": 1, "last_four_result": "not_provided",
            "answers": {"answers": [a("recording_consent", "yes", 1), a("change_requested", "no", 5), a("flag", "other", 6)]}}
    assert earlier_call(body) == EarlierCall(result="failed", recording_consent=("yes",), change_requested=("no",),
                                             last_four_result="not_provided", flags=("other",))
    assert earlier_call({"call_id": "c2", "result": "no_answer", "attempt": 2}) == EarlierCall(result="no_answer")


def test_the_hash_covers_earlier_calls_and_answer_order_but_not_flag_order():
    base = facts_from_answers(CALL, call_result="answers_recorded", dialed_eligible_number=True)
    said_no = CaseFacts(**{**base.__dict__, "earlier_calls": (EarlierCall(result="failed", change_requested=("no",)),)})
    assert inputs_hash(said_no) != inputs_hash(base)
    flipped = CaseFacts(**{**base.__dict__, "change_requested": ("no", "yes")})
    assert inputs_hash(CaseFacts(**{**base.__dict__, "change_requested": ("yes", "no")})) != inputs_hash(flipped)
    ab = CaseFacts(**{**base.__dict__, "flags": ("a", "b")})
    assert inputs_hash(ab) == inputs_hash(CaseFacts(**{**base.__dict__, "flags": ("b", "a")}))


def test_a_name_we_cant_read_could_be_anyone():
    assert same_person([*CALL, a("speaker_name", "?", 8)]) is None
    assert same_person([*CALL, a("speaker_name", "?", 8), a("speaker_name", "Sam Ortiz", 9)]) is False, "two readable names"


def test_answers_are_judged_by_turn_not_by_list_order():
    shuffled = list(reversed(CALL))
    assert (consent_first(shuffled), answers_in_order(shuffled), same_person(shuffled)) == (True, True, True)
    asked_again = [*CALL, a("recording_consent", "yes", 11)]  # consent repeated later is still first
    assert consent_first(asked_again) is True


def test_order_facts_are_unknown_if_any_answer_has_no_turn():
    partial = [*CALL[:-1], {"slot": "account_last4", "value": "6666"}]
    f = facts_from_answers(partial, call_result="answers_recorded", dialed_eligible_number=True)
    assert (f.consent_first, f.answers_in_order, f.same_person_yes_and_digits) == (None, None, None)
