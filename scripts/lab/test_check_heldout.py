"""The sealed-set checker's own rules (spec 0008). The real scenario format is Sami's and lives
outside this branch, so a cut-down schema stands in for it here.

    uv run --with jsonschema --with pytest pytest scripts/lab/test_check_heldout.py
"""
from __future__ import annotations

import copy

from check_heldout import merge_patch, problems

PHONE = {"type": "object", "required": ["e164"], "properties": {"e164": {"type": "string"}}}
SCHEMA = {
    "type": "object",
    "required": ["family_id", "held_out", "facts", "variants"],
    "properties": {
        "facts": {"type": "object", "required": ["phone_numbers"],
                  "properties": {"phone_numbers": {"type": "array", "items": PHONE}}},
        "variants": {"type": "array", "items": {"type": "object", "required": ["variant_id"]}},
    },
}
FAMILY = {
    "family_id": "ho-test", "held_out": True,
    "facts": {"phone_numbers": [{"e164": "+14155550190", "persona_ref": "a"}]},
    "variants": [{"variant_id": "base"},
                 {"variant_id": "other-persona", "overrides": {"phone_numbers": [{"e164": "+14155550191", "persona_ref": "b"}]}}],
}


def check(change=None) -> list[str]:
    fam = copy.deepcopy(FAMILY)
    if change:
        change(fam)
    return problems([fam], SCHEMA)


def test_a_clean_family_has_no_problems():
    assert check() == []


def test_merge_patch_follows_rfc_7396():
    assert merge_patch({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": None, "e": 4}, "f": [1]}) == {"a": 1, "b": {"d": 3, "e": 4}, "f": [1]}


def test_each_rule_is_reported():
    cases = {
        "duplicate variant_id": lambda f: f["variants"].append({"variant_id": "other-persona", "overrides": {"x": 1}}),
        "changes nothing": lambda f: f["variants"].append({"variant_id": "same"}),
        "has an expected block": lambda f: f["variants"][0].update(expected={}),
        "after overrides": lambda f: f["variants"][1].update(overrides={"phone_numbers": None}),
        "not a fictional": lambda f: f["variants"][1]["overrides"]["phone_numbers"][0].update(e164="+14155551234"),
        "held_out must be true": lambda f: f.update(held_out=False),
    }
    for expected, change in cases.items():
        found = check(change)
        assert any(expected in p for p in found), (expected, found)


def test_a_null_phone_list_is_reported_not_a_crash():
    found = check(lambda f: f["facts"].update(phone_numbers=None))
    assert any("phone_numbers" in p or "None is not" in p for p in found), found
