"""The sentence for each reason a number isn't eligible (spec 0023).

The dashboard shows these and never writes its own. They say "provable" or "not provable",
never "safe", and name no number or setting, so they read the same for every case.
"""
from __future__ import annotations

REASON_TEXT: dict[str, str] = {
    "first_seen_in_change_context": "Not provable: this number first appeared in the change request, its thread, a "
                                    "lookalike domain or after the request, which is where a fraudster's number would come from.",
    "too_few_source_types": "Not provable: it wasn't seen in at least two independent channels before the request "
                            "(all email counts as one channel, and the old number on file never counts).",
    "younger_than_minimum_age": "Not provable: it was first seen too recently before the request; the rule needs a "
                                "number the vendor has used for months.",
    "near_miss_of_older_number": "Not provable: it is one digit off, or two swapped digits away from, an older number "
                                 "of this vendor.",
    "hidden_text_only": "Not provable: it appeared only in text a person couldn't see (white, tiny, off the page, or "
                        "leaving no ink on the page).",
    "unconfirmed_digits_only": "Not provable: it appeared where a person could see it, but only in text whose digits "
                               "couldn't be confirmed against the page.",
    "not_vendor_party": "Not provable: the documents label it as someone else's number, such as a bank's or a "
                        "customer's, or their labels disagree.",
    "fax_line": "Not provable: the documents label it as a fax line.",
}

# The lineage panel's other states (spec 0023): the dashboard shows these and writes none itself.
NOTES: dict[str, str] = {
    "no_selection": "No number selection is recorded for this case yet. Until the Trust Ledger records one, "
                    "no call is made and the hold stays on.",
    "no_provable_number": "No call is made. The hold stays on and the case waits for a person.",
    "alternates": "Alternates are used only if the chosen line is dead.",
}
