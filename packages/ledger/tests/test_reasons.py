"""Spec 0023: one template sentence per reason code, in the dashboard's vocabulary."""
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


def test_the_notes_never_say_safe():
    from dialback_ledger.reasons import NOTES
    assert set(NOTES) == {"no_selection", "no_provable_number", "alternates"}
    assert all("safe" not in text.lower() for text in NOTES.values())


def test_every_sentence_exactly():
    # The dashboard serves the current text for every stored selection, whatever its rule version, so a
    # change to any sentence is a decision, made here on purpose (spec 0091 changed two).
    assert REASON_TEXT == {
        "first_seen_in_change_context": "Not provable: this number first appeared in the change request, its thread, a "
                                        "lookalike domain or after the request, which is where a fraudster's number "
                                        "would come from.",
        "too_few_source_types": "Not provable: it wasn't seen as the vendor's number in at least two independent "
                                "channels before the request (all email counts as one channel, and the old number on "
                                "file never counts).",
        "younger_than_minimum_age": "Not provable: it was first seen as the vendor's number too recently before the "
                                    "request; the rule needs a number the vendor has used for months.",
        "near_miss_of_older_number": "Not provable: it is one digit off, or two swapped digits away from, an older "
                                     "number of this vendor.",
        "hidden_text_only": "Not provable: it appeared only in text a person couldn't see (white, tiny or off the "
                            "page).",
        "not_vendor_party": "Not provable: the documents label it as someone else's number, such as a bank's or a "
                            "customer's, or their labels disagree.",
        "fax_line": "Not provable: the documents label it as a fax line.",
    }
