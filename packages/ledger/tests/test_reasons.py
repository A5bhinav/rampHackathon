"""Spec 0017: one template sentence per reason code, in the dashboard's vocabulary."""
import json
from pathlib import Path

from dialback_ledger.reasons import REASON_TEXT

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts/schemas/selection.schema.json").read_text())


def test_every_reason_code_has_a_sentence_and_nothing_else():
    assert set(REASON_TEXT) == set(SCHEMA["$defs"]["Reason"]["enum"])


def test_sentences_say_not_provable_and_never_safe():
    for code, text in REASON_TEXT.items():
        assert text.startswith("Not provable:"), code
        assert "safe" not in text.lower(), code
