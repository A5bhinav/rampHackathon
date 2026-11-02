"""The worked examples (spec 0006, acceptance). Each fixture in `examples/` is one call: the
answers the voice service recorded, plus whatever the call changes from the clean call's
computed facts in `_base.json`. The facts are built the way the core builds them (the same
helpers, the same last-four comparison), so the examples test the derivations too.
The attack lab may reuse these fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from dialback_decision import CallChecks, compare_last_four, decide
from dialback_decision.facts import earlier_call, facts_from_answers, values

HERE = Path(__file__).parent / "examples"
BASE = json.loads((HERE / "_base.json").read_text())
EXAMPLES = sorted(HERE.glob("[0-9]*.json"))


def _answers(rows: list[list]) -> list[dict]:
    return [{"slot": slot, "value": value, "turn_id": turn} for slot, value, turn in rows]


def _last_four(answers: list[dict]) -> str | None:
    if not answers:
        return None
    return compare_last_four(values(answers, "account_last4"), BASE["accounts"]["new_last4"], BASE["accounts"]["old_last4"])


def build(example: dict):
    answers = _answers(example["answers"])
    earlier = tuple(
        earlier_call({"result": c["result"], "answers": {"answers": _answers(c["answers"])},
                      "last_four_result": _last_four(_answers(c["answers"]))})
        for c in example.get("earlier_calls", ()))
    return facts_from_answers(answers, last_four_result=_last_four(answers),
                              checks=CallChecks(**(BASE["checks"] | example.get("checks", {}))),
                              earlier_calls=earlier, **(BASE["known"] | example.get("known", {})))


def test_there_are_worked_examples_for_the_spec():
    assert len(EXAMPLES) == 24, "21 examples, three of them in two variants"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_worked_example(path: Path):
    example = json.loads(path.read_text())
    d = decide(build(example), strict=example.get("strict", False), source=example.get("source", "call"))
    assert (d.outcome, list(d.rules_fired)) == (example["expect"]["outcome"], example["expect"]["rules_fired"]), example["call"]
