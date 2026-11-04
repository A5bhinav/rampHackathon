"""The model's whole action space: four tools that only record (design, "Tools"; ADR-4).

No tool sets an outcome, picks or changes a number, or touches a hold or a payment. Arguments
are validated strictly: exact fields, slot names from a fixed list, values typed per slot (the
same rules as `call-answers.schema.json`, checked by `tests/test_contract.py`), and a citation
of a callee turn on every answer. `validate` raises `ToolError`; `step` counts it as a policy
violation (first: ask again; second: end the call).
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SLOTS = ("recording_consent", "company_named", "department", "speaker_name", "speaker_role", "change_requested",
         "account_last4", "offered_number", "flag")
FLAGS = ("pressure_to_approve", "opt_out", "asked_what_is_checked", "asked_to_call_another_number",
         "speaker_changed", "other")
NO_DIGITS = ("dont_know", "refused")
END_REASONS = ("completed", "wrong_company", "callee_declined", "opt_out", "other")
MAX_KEY_PRESSES = 5

_FIELDS = {
    "record_answer": {"slot", "value", "turn_id"},
    "schedule_callback": {"window"},
    "end_call": {"reason"},
    "press_keys": {"digits"},
}
_TEXT_SLOTS = {"company_named", "department", "speaker_name", "speaker_role"}
_LAST4 = re.compile(r"[0-9]{1,4}")  # used with fullmatch: "4821\n" is not 4821
_KEYS = re.compile(r"[0-9*#]{1,3}")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: Mapping[str, Any]


class ToolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def value_ok(slot: str, value: Any) -> bool:
    """The value rules of the in-transit call answers, slot by slot."""
    if not isinstance(value, str):
        return False
    if slot == "recording_consent":
        return value in ("yes", "no")
    if slot == "change_requested":
        return value in ("yes", "no", "unsure")
    if slot in _TEXT_SLOTS:
        return 1 <= len(value) <= 200
    if slot == "account_last4":
        return value in NO_DIGITS or bool(_LAST4.fullmatch(value))
    if slot == "offered_number":
        return 1 <= len(value) <= 32
    if slot == "flag":
        return value in FLAGS
    return False


def _moment(raw: Any, field: str) -> dt.datetime:
    if not isinstance(raw, str):
        raise ToolError("bad_value", f"{field} must be an ISO 8601 time with an offset")
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise ToolError("bad_value", f"{field} must be an ISO 8601 time with an offset") from None
    if moment.tzinfo is None:
        raise ToolError("bad_value", f"{field} has no offset")
    return moment


def validate(call: ToolCall, *, callee_turns: frozenset[int], in_menu: bool = False,
             keys_used: int = 0) -> dict[str, Any]:
    """Check one tool call and return its arguments, parsed. Raises ToolError."""
    if call.name not in _FIELDS:
        raise ToolError("unknown_tool", f"no tool named {call.name!r}")
    if not isinstance(call.id, str) or not 1 <= len(call.id) <= 128:
        raise ToolError("bad_arguments", "the tool call id must be 1 to 128 characters")
    if not isinstance(call.args, Mapping) or set(call.args) != _FIELDS[call.name]:
        raise ToolError("bad_arguments", f"{call.name} takes exactly {sorted(_FIELDS[call.name])}")
    args = dict(call.args)

    if call.name == "record_answer":
        if args["slot"] not in SLOTS:
            raise ToolError("bad_slot", f"no slot named {args['slot']!r}")
        turn = args["turn_id"]
        if type(turn) is not int or turn not in callee_turns:
            raise ToolError("bad_turn", "turn_id must cite a turn the callee spoke")
        if not value_ok(args["slot"], args["value"]):
            raise ToolError("bad_value", f"not an allowed value for {args['slot']}")
        return args

    if call.name == "schedule_callback":
        window = args["window"]
        if window is None:
            return {"window": None}
        if not isinstance(window, Mapping) or "start" not in window or not set(window) <= {"start", "end"}:
            raise ToolError("bad_arguments", "window is null or {start, end?}; there is no number field")
        start = _moment(window["start"], "window.start")
        end = _moment(window["end"], "window.end") if window.get("end") is not None else None
        if end is not None and end <= start:
            raise ToolError("bad_value", "window.end must be after window.start")
        return {"window": {"start": start, "end": end}}

    if call.name == "end_call":
        if args["reason"] not in END_REASONS:
            raise ToolError("bad_value", f"reason must be one of {END_REASONS}")
        return args

    # press_keys: phone menus only, a few keys at a time, a handful of uses.
    if not in_menu:
        raise ToolError("not_in_menu", "press_keys works only in a phone menu")
    if keys_used >= MAX_KEY_PRESSES:
        raise ToolError("key_cap_reached", f"press_keys is limited to {MAX_KEY_PRESSES} uses")
    if not isinstance(args["digits"], str) or not _KEYS.fullmatch(args["digits"]):
        raise ToolError("bad_value", "digits must be 1 to 3 of 0-9, * and #")
    return args
