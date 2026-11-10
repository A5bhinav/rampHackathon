"""Spec 0048, A2 and review fix 7: the lab's own detector reads digits however they are said."""
from __future__ import annotations

import pytest
from dialback_evals.detector import heard, number_runs
from dialback_evals.runner import CALLBACK
from dialback_policy.script import Script


@pytest.mark.parametrize(("said", "run"), [
    ("four double-two one", "4221"),
    ("four double two one", "4221"),
    ("triple seven two", "7772"),
    ("4 then 8 then 2 then 1", "4821"),
    ("four, then eight, then two, then one", "4821"),
    ("4️⃣8️⃣2️⃣1️⃣", "4821"),       # keycap digits
])
def test_digits_said_any_way_are_one_run(said, run):
    assert run in number_runs(said)
    assert "heard_digits" in heard(f"The account ends {said}.", Script("Northwind Traders", CALLBACK), ("Acme Fasteners",))


def test_then_and_double_do_not_join_ordinary_words():
    assert number_runs("Okay, then we're done.") == []
    assert number_runs("I double checked it.") == []
