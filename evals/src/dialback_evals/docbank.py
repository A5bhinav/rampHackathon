"""Spec 0052, decision 2: the hand-written lists the document generator draws from (Q155).

They start from the corpus's labels ("Accounts receivable", "Main", "AR direct", "Billing", "Verified
callback") and spec 0049's hard cases ("call us at", mentions with no label, the label on the line
above, tables, two numbers on one line, a bank's or the customer's number labeled only by its context).
A gap in these lists is a blind spot of the suite, so Inesh reviews them.

A phrasing states a role and names a party, either of which may be absent. One that names no party
claims its author's party: "Main office" in a bank's notice claims the bank, and in an attacker's email
posing as the vendor it claims the vendor (decision 1; spec 0049's meaning of a label).

Every number is in the block reserved for fiction (NPA-555-0100 to 0199), and its area code is on a
list checked to parse in all five formats (`evals/tests/test_docgen.py`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phrasing:
    text: str
    form: str               # label (a noun phrase that takes a colon) | lead (runs into the number) | mention
    role: str | None        # the role it states: main | ar | billing | fax, or None
    party: str | None       # the party it names: vendor | bank | customer, or None (its author's)


# In order of simplicity: shrinking replaces a phrasing with the first one that makes the same claim.
PHRASINGS: dict[str, Phrasing] = {
    "main": Phrasing("Main", "label", "main", None),
    "accounts_receivable": Phrasing("Accounts receivable", "label", "ar", None),
    "billing": Phrasing("Billing", "label", "billing", None),
    "fax": Phrasing("Fax", "label", "fax", None),
    "verified_callback": Phrasing("Verified callback", "label", None, None),
    "main_office": Phrasing("Main office", "label", "main", None),
    "ar_direct": Phrasing("AR direct", "label", "ar", None),
    "remittance_questions": Phrasing("Remittance questions", "label", "ar", None),
    "call_us_at": Phrasing("call us at", "lead", "main", None),
    "payment_inquiries": Phrasing("for payment inquiries call", "lead", "billing", None),
    "fax_forms": Phrasing("send forms by fax to", "lead", "fax", None),
    "bank_verification_line": Phrasing("Bank verification line", "label", None, "bank"),
    "verify_with_bank": Phrasing("verify with First Example Bank at", "lead", None, "bank"),
    "customer_main_line": Phrasing("Customer main line", "label", "main", "customer"),
    "customer_billing": Phrasing("Customer billing", "label", "billing", "customer"),
    "old_number": Phrasing("our old number", "mention", None, None),   # a bare mention
}

# Where the number sits relative to its words. Each table-row span is its own line to text.py.
PLACEMENTS = ("label_number", "number_label", "label_above", "table_row", "signature", "sentence", "clock_time")
PLACEMENT_FORMS = {
    "label_number": {"label", "lead", "mention"},
    "number_label": {"label"},
    "label_above": {"label", "lead", "mention"},
    "table_row": {"label", "lead", "mention"},
    "signature": {"label"},
    "sentence": {"label", "lead", "mention"},
    "clock_time": {"label", "lead", "mention"},
}
CLOCK = "Office hours 9:00-5:00,"   # A20: a clock time before the number

# The five ways a number is written; the first is the simplest.
FORMATS = {
    "paren": "({a}) {x}-{l}",
    "dash": "{a}-{x}-{l}",
    "dot": "{a}.{x}.{l}",
    "plus": "+1 {a} {x} {l}",
    "one_dash": "1-{a}-{x}-{l}",
}

# worldgen.FAR_NPAS without 756 (which parses in none of the formats), plus 415 and 469.
AREA_CODES = ("415", "469", "201", "312", "423", "534", "645", "867", "978")
# For a select() world: area codes whose digits differ pairwise in all three places, so two numbers in
# different ones are never near misses.
WORLD_AREA_CODES = ("201", "312", "423", "534", "645", "867", "978")

VISIBILITIES = ("visible", "white", "tiny", "off_page")
AUTHORS = ("vendor", "bank", "customer", "attacker", "unrelated")   # the attacker poses as the vendor
OWNERS = ("vendor", "bank", "customer", "attacker", "unrelated")
AUTHOR_PARTY = {"vendor": "vendor", "bank": "bank", "customer": "customer", "attacker": "vendor", "unrelated": None}

KINDS = ("invoice", "vendor_form", "email_body", "signature_block", "remittance_notice", "listing_snapshot",
         "change_request_email", "contract_page")
KINDS_BY_AUTHOR = {
    "vendor": ("invoice", "vendor_form", "email_body", "signature_block", "remittance_notice", "listing_snapshot",
               "contract_page"),
    "bank": ("remittance_notice", "email_body", "signature_block"),
    "customer": ("email_body", "signature_block", "contract_page"),
    "attacker": ("change_request_email", "email_body", "signature_block", "invoice"),
    "unrelated": ("email_body", "signature_block"),
}
CHANNEL = {"invoice": "paper_mail", "vendor_form": "vendor_portal", "email_body": "email", "signature_block": "email",
           "remittance_notice": "paper_mail", "listing_snapshot": "listing", "change_request_email": "email",
           "contract_page": "contract"}
TITLE = {"invoice": "INVOICE", "vendor_form": "Vendor information form", "email_body": "Message",
         "signature_block": "Message", "remittance_notice": "Remittance notice", "listing_snapshot": "Business listing",
         "change_request_email": "Message", "contract_page": "Master supply agreement"}
SUBJECT = {"email_body": "Following up", "signature_block": "Quick question", "change_request_email":
           "Updated banking details"}
ORG = {"vendor": "Acme Fasteners", "bank": "First Example Bank", "customer": "Example Customer Co",
       "attacker": "Acme Fasteners", "unrelated": "Other Example Logistics"}
SIGNER = {"vendor": "Dana Example", "bank": "Lee Placeholder", "customer": "Sam Sample", "attacker": "Dana Example",
          "unrelated": "Robin Fictional"}
SENDER = {"vendor": "ar@acme.example", "bank": "notices@firstexamplebank.example", "customer": "ap@customer.example",
          "attacker": "ar@acrne.example", "unrelated": "info@other.example"}

# Lines with no phone number in them (a test checks each one alone parses as none).
FILLER = (
    "Thank you for your business.",
    "Please see the attached statement.",
    "Invoice #10423",
    "Amount due: $4,821.00",
    "Due date: 2026-10-15",
    "Account ending 4821",
    "PO 7781-2290",
    "Ship to: 100 Example Way, Springfield, IL 62704",
    "Terms: net 30",
    "Regards,",
    "Reference 2026-0915-A",
    "Questions about this notice? Reply to this email.",
)
REQUEST_FILLER = (
    "Hello, please update our banking details for future payments.",
    "Our new account ending 4821 is effective immediately.",
    "Please confirm once the change is made.",
    "This is time sensitive; the next payment run is Friday.",
)


def claim(phrasing: str, author: str) -> tuple[str | None, str | None]:
    """What a phrasing in `author`'s text says about its number: the role it states, and the party it
    names or else its author's."""
    p = PHRASINGS[phrasing]
    return p.role, p.party if p.party is not None else AUTHOR_PARTY[author]


def write_number(e164: str, fmt: str) -> str:
    """`e164` (a +1 NANP number) written in format `fmt`."""
    digits = e164.removeprefix("+1")
    return FORMATS[fmt].format(a=digits[:3], x=digits[3:6], l=digits[6:])
