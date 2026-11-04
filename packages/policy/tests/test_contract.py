"""The tool checks and call-answers.schema.json must agree, so the two can't drift."""
import json
from pathlib import Path

import pytest
from dialback_policy.tools import FLAGS, SLOTS, value_ok
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMAS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "schemas"
PREFIX = "https://dialback.dev/schemas/"


def _registry() -> Registry:
    def retrieve(uri: str) -> Resource:
        return Resource.from_contents(json.loads((SCHEMAS / uri.removeprefix(PREFIX)).read_text()),
                                      default_specification=DRAFT202012)
    return Registry(retrieve=retrieve)


SCHEMA = json.loads((SCHEMAS / "call-answers.schema.json").read_text())
TRANSIT = Draft202012Validator({"$ref": f"{PREFIX}call-answers.schema.json#/$defs/TransitAnswer"}, registry=_registry())


def transit_ok(slot: str, value: object) -> bool:
    return TRANSIT.is_valid({"slot": slot, "value": value, "turn_id": 1, "tool_call_id": "tc_1",
                             "captured_at": "2026-11-04T17:00:00Z", "capture_method": "speech"})


def test_slots_match_the_contract() -> None:
    assert tuple(SCHEMA["$defs"]["Slot"]["enum"]) == SLOTS


def test_flags_match_the_contract() -> None:
    flag_rule = next(r for r in SCHEMA["$defs"]["AnswerBase"]["allOf"] if r["if"]["properties"]["slot"].get("const") == "flag")
    assert tuple(flag_rule["then"]["properties"]["value"]["enum"]) == FLAGS


SAMPLES = [
    ("recording_consent", "yes"), ("recording_consent", "no"), ("recording_consent", "sure"),
    ("company_named", "Acme"), ("company_named", ""), ("company_named", "x" * 201), ("company_named", 7),
    ("change_requested", "unsure"), ("change_requested", "maybe"),
    ("account_last4", "4821"), ("account_last4", "1"), ("account_last4", "48210"), ("account_last4", "dont_know"),
    ("account_last4", "refused"), ("account_last4", "48a1"), ("account_last4", 4821),
    ("offered_number", "415 555 0177"), ("offered_number", "1" * 33), ("offered_number", ""),
    ("flag", "speaker_changed"), ("flag", "approve_now"),
]


@pytest.mark.parametrize(("slot", "value"), SAMPLES)
def test_value_checks_agree_with_the_in_transit_shape(slot: str, value: object) -> None:
    assert value_ok(slot, value) == transit_ok(slot, value)
