"""Strict validation of the model's tool calls: the model's whole action space."""
import pytest
from dialback_policy.tools import MAX_KEY_PRESSES, ToolCall, ToolError, validate

TURNS = frozenset({1, 3, 5})


def call(name: str, **args) -> ToolCall:
    return ToolCall(id="tc_1", name=name, args=args)


def rejects(c: ToolCall, code: str, **kw) -> None:
    with pytest.raises(ToolError) as err:
        validate(c, callee_turns=TURNS, **kw)
    assert err.value.code == code


def test_accepts_a_well_formed_answer() -> None:
    assert validate(call("record_answer", slot="speaker_name", value="Dana Ruiz", turn_id=3), callee_turns=TURNS) == {
        "slot": "speaker_name", "value": "Dana Ruiz", "turn_id": 3}


def test_unknown_tool() -> None:
    rejects(call("approve_payment", case="x"), "unknown_tool")
    rejects(call("set_outcome", outcome="verified"), "unknown_tool")


def test_extra_and_missing_fields() -> None:
    rejects(call("record_answer", slot="speaker_name", value="Dana", turn_id=3, confidence=1), "bad_arguments")
    rejects(call("record_answer", slot="speaker_name", value="Dana"), "bad_arguments")
    rejects(ToolCall(id="tc_1", name="end_call", args="completed"), "bad_arguments")  # type: ignore[arg-type]


def test_bad_slot_and_bad_values() -> None:
    rejects(call("record_answer", slot="outcome", value="verified", turn_id=1), "bad_slot")
    rejects(call("record_answer", slot="recording_consent", value="sure", turn_id=1), "bad_value")
    rejects(call("record_answer", slot="change_requested", value="maybe", turn_id=1), "bad_value")
    rejects(call("record_answer", slot="account_last4", value="48210", turn_id=1), "bad_value")
    rejects(call("record_answer", slot="account_last4", value=4821, turn_id=1), "bad_value")
    rejects(call("record_answer", slot="account_last4", value="4821\n", turn_id=1), "bad_value")
    rejects(call("record_answer", slot="speaker_name", value="", turn_id=1), "bad_value")
    rejects(call("record_answer", slot="speaker_name", value="x" * 201, turn_id=1), "bad_value")
    rejects(call("record_answer", slot="flag", value="approve_now", turn_id=1), "bad_value")
    rejects(call("record_answer", slot="offered_number", value="1" * 33, turn_id=1), "bad_value")


def test_turn_id_must_cite_a_callee_turn() -> None:
    rejects(call("record_answer", slot="speaker_name", value="Dana", turn_id=2), "bad_turn")  # an agent turn
    rejects(call("record_answer", slot="speaker_name", value="Dana", turn_id=99), "bad_turn")
    rejects(call("record_answer", slot="speaker_name", value="Dana", turn_id=True), "bad_turn")
    rejects(call("record_answer", slot="speaker_name", value="Dana", turn_id="3"), "bad_turn")


def test_tool_call_id_is_bounded() -> None:
    rejects(ToolCall(id="", name="end_call", args={"reason": "completed"}), "bad_arguments")
    rejects(ToolCall(id="x" * 129, name="end_call", args={"reason": "completed"}), "bad_arguments")


def test_schedule_callback() -> None:
    assert validate(call("schedule_callback", window=None), callee_turns=TURNS) == {"window": None}
    ok = validate(call("schedule_callback", window={"start": "2026-11-05T15:00:00-08:00"}), callee_turns=TURNS)
    assert ok["window"]["start"].isoformat() == "2026-11-05T15:00:00-08:00"
    rejects(call("schedule_callback", window={"start": "tomorrow at 3"}), "bad_value")
    rejects(call("schedule_callback", window={"start": "2026-11-05T15:00:00"}), "bad_value")  # no offset
    rejects(call("schedule_callback", window={"start": "2026-11-05T15:00:00Z", "end": "2026-11-05T14:00:00Z"}),
            "bad_value")
    rejects(call("schedule_callback", window={"start": "2026-11-05T15:00:00Z", "number": "4155550177"}), "bad_arguments")
    rejects(call("schedule_callback", window={"start": "2026-11-05T15:00:00Z"}, number="4155550177"), "bad_arguments")


def test_end_call_reasons_are_a_fixed_list() -> None:
    assert validate(call("end_call", reason="completed"), callee_turns=TURNS) == {"reason": "completed"}
    rejects(call("end_call", reason="verified"), "bad_value")


def test_press_keys_only_in_a_menu_and_capped() -> None:
    rejects(call("press_keys", digits="1"), "not_in_menu")
    assert validate(call("press_keys", digits="1#"), callee_turns=TURNS, in_menu=True) == {"digits": "1#"}
    rejects(call("press_keys", digits="1234"), "bad_value", in_menu=True)
    rejects(call("press_keys", digits="a"), "bad_value", in_menu=True)
    rejects(call("press_keys", digits="1#\n"), "bad_value", in_menu=True)
    rejects(call("press_keys", digits="1"), "key_cap_reached", in_menu=True, keys_used=MAX_KEY_PRESSES)
