"""Flagging, bank-detail extraction and the ledger's text for the email door (spec 0086, Task 6)."""
from __future__ import annotations

import hashlib
import hmac
import random
import re
import time
import unicodedata

import phonenumbers as pn
import pytest
from dialback_core.bank import aba_valid
from dialback_core.email_door import rules
from dialback_core.email_door.masking import DIGIT_RUN
from dialback_core.email_door.rules import (
    Flag,
    find_accounts,
    find_routing,
    flag,
    ledger_text,
    requested_fingerprint,
)
from dialback_ledger.candidates import find_phones, parse_phone
from dialback_ledger.reader import StubReader
from dialback_ledger.text import Line

ACCOUNT = "000123454821"


def five_digit_pieces_of(account: str) -> set[str]:
    return {account[i:i + 5] for i in range(len(account) - 4)}


def surviving_account_digits(text: str, account: str = ACCOUNT) -> set[str]:
    """Every five-digit stretch of `account` that can still be read in `text`, separators ignored."""
    found = set()
    for m in re.finditer(r"\d(?:[^\d*]{0,5}\d)*", text):
        digits = re.sub(r"\D", "", m.group(0))
        found |= {digits[i:i + 5] for i in range(len(digits) - 4)} & five_digit_pieces_of(account)
    return found


def numeric_runs_outside_the_phones(quote: str) -> list[str]:
    """Every run of 5 or more numeric characters (str.isnumeric, so CJK numerals count; up to 5 separator
    characters between them) in `quote` that lies outside the phone numbers the ledger's matcher reads there."""
    chars = list(quote)
    for c in find_phones([Line(quote, "visible", 0, 0)]):
        chars[c.start:c.end] = " " * (c.end - c.start)
    runs, run, gap = [], "", 0
    for ch in chars + ["\n"]:
        if ch.isnumeric():
            run, gap = run + ch, 0
        elif run and gap < 5 and not ch.isalnum() and ch != "*":
            gap += 1
        else:
            if len(run) >= 5:
                runs.append(run)
            run, gap = "", 0
    return runs


def numerics(text: str) -> str:
    return "".join(c for c in text if c.isnumeric())


def digits_of(e164: str) -> set[str]:
    """The digit strings a line holding only `e164` may carry: with the country code or the national part."""
    return {e164.lstrip("+"), pn.national_significant_number(pn.parse(e164))}


def stored(text: str) -> list[tuple[str, str]]:
    """(quote, phone) for every line the ledger's stub reader would store from the ledger's text of `text`."""
    return [(label["quote"], parse_phone(label["quote"])[0]) for label in StubReader().label(ledger_text(text))]


def phones_on_the_lines_of(text: str) -> set[str]:
    return {phone for line in text.splitlines() for phone in parse_phone(line)}


# Flagging ------------------------------------------------------------------------------------------

def test_flag_pairs_a_change_word_with_a_payment_destination_in_a_sentence_or_a_core_verb_in_the_email():
    assert flag("Banking update", "Hi, see below.").flagged
    assert flag("Re: Invoice 1638", "We have moved our banking. Please pay the account ending 4821.").flagged
    assert flag("", "Our account number changed when our bank merged.").flagged
    # A change word with no payment word, and a payment word with no change word.
    assert not flag("Price list update", "Our new price list is effective Jan 1.").flagged
    assert not flag("Status of ACH", "Our bank shows no deposit yet from the ACH you sent Monday.").flagged
    # A change adjective must sit on the destination word itself.
    assert not flag("", "Our new office opens Monday. Thanks for paying invoice 1702 to our bank so quickly.").flagged
    # Revision 1 (after version 5 failed the held-out bar): a core change verb pairs with a destination
    # word anywhere in the email, because the change is often announced first and the details given after.
    assert flag("", "Our details have changed.\n\nBank: First Meridian\nRouting: 026009593").flagged
    assert not flag("", "Nothing has changed on our side.\n\nBank: First Meridian\nRouting: 026009593").flagged
    result = flag("Updated wire instructions", "")
    assert result == Flag(True, result.reasons) and result.reasons
    assert not any(c.isdigit() for r in result.reasons for c in r)


def test_flag_skips_negated_hypothetical_and_how_to_sentences():
    assert not flag("Reminder", "Never change a vendor's bank details because an email asked you to.").flagged
    assert not flag("Process question", "If we ever need to change our bank account, what is your procedure?").flagged
    assert not flag("Webinar", "Learn how criminals change vendor bank details.").flagged
    assert not flag("Invoice 3391", "Payment instructions are unchanged: ACH to routing 021000021.").flagged
    assert not flag("Invoice 3392", "No changes to our bank details this quarter.").flagged
    # A negated sentence does not hide a real request in the next one.
    assert flag("Invoice 3393", "Nothing else has changed. Please update our bank details to the new account below.").flagged
    assert not flag("Auto reply", "For changes to bank details, call your account manager.").flagged
    assert not flag("", "In case you ever need to update our remittance details, use the portal.").flagged
    # Talk about fraud describes requests instead of making one.
    assert not flag("Fraud alert", "Fraudsters send emails asking you to update vendor bank details.").flagged
    # Some negations still ask.
    assert flag("", "Don't forget to update our bank details before Friday's run.").flagged


def test_a_correction_to_the_details_is_a_change():
    assert flag("Re: Invoice 2188", "The bank info on that invoice has a typo in the account.").flagged
    assert flag("", "The ACH instructions in our last invoice are wrong, please do not use them.").flagged
    # "The wrong account" is a payment that went astray, not new details.
    assert not flag("Missing payment", "Did our payment go to the wrong account last week?").flagged


@pytest.mark.parametrize(("flagged", "subject", "text"), [
    (True, "", "Our account at First National is frozen, please pay into the account below."),
    (True, "", "Please pay the account ending 4821 from today."),
    (True, "Remittance update", "See attached letter."),
    (True, "", "Kindly be informed that we have changed our bankers."),
    (True, "", "Please process our bank change request today."),
    (True, "", "Our routing number remains the same, but the account number has changed."),
    (True, "", "We have a new mailing address for checks: PO Box 1234, Dayton OH."),
    (False, "Payment failed", "Your card was declined. Please update your payment method."),
    (False, "Welcome", "Your new customer account is ready."),
    (False, "Thanks", "We received the new remittance this morning."),
    (False, "Newsletter", "Verify all bank change requests by phone."),
    (False, "", "We have changed our company name, bank account remains the same."),
    (False, "Shipment update", "We changed the routing of your shipment through Memphis."),
    (False, "Invoice 5521", "Remit to New York Community Bank, routing 021000021, account 1234567."),
    (False, "Holiday", "Our bank will be closed on Monday; payments will post Tuesday."),
    # "Moving forward" is a time phrase, not the verb "move" (fixed after the stop, found by code reading).
    (False, "", "Moving forward, send invoices to ap@x.example.\n\nRemit to: First Meridian Bank, routing 026009593"),
])
def test_flag_examples(flagged, subject, text):
    assert flag(subject, text).flagged is flagged


def test_flag_is_fast_on_a_long_unpunctuated_email():
    text = ("we changed the bank account and the new routing details for remittance going forward " * 3000)[:200_000]
    start = time.perf_counter()
    flag("Banking update", text)
    assert time.perf_counter() - start < 1.0


def test_flag_reads_only_the_first_50000_characters():
    assert flag("", "We have changed our bank.").flagged
    assert not flag("", "x " * 25_000 + "We have changed our bank.").flagged
    assert not flag("y" * 50_000, "We have changed our bank.").flagged


def test_quoted_paragraphs_split_like_plain_ones():
    plain = "Thanks, we moved\n\nBank statements for October are attached"
    quoted = "> Thanks, we moved\n>\n> Bank statements for October are attached"
    assert not flag("", plain).flagged and not flag("", quoted).flagged


def test_an_invoice_with_remit_to_details_is_not_flagged():
    text = ("Hello,\n\nPlease find invoice 1744 attached for $4,820.00, net 30.\n\n"
            "Remit to: Acme Fasteners LLC\nBank: First National\nRouting: 021000021\nAccount: 0000 4444 5555\n\n"
            "Thank you for your business.")
    assert not flag("Invoice 1744 from Acme Fasteners LLC", text).flagged


def test_forwarded_request_is_flagged():
    text = ("Can you action this?\n\n---------- Forwarded message ---------\n"
            "From: Rick Alvarado <controller@acme.example>\nDate: Tue, Oct 13, 2026\nSubject: hello\n\n"
            "> Dana, from next week we are switching\n"
            "> banks for customer payments. Remit to routing 026009593,\n"
            "> account 0001 2345 4821.\n")
    # Nothing above the separator or in the subject asks for anything: the request is only in the quoted
    # original, with its change word and payment word on different quoted lines.
    assert not flag("Fwd: hello", "Can you action this?").flagged
    result = flag("Fwd: hello", text)
    assert result.flagged
    assert any(r.startswith("body") for r in result.reasons)


# Routing numbers -----------------------------------------------------------------------------------

def test_aba_valid_refuses_a_trailing_newline():
    assert not aba_valid("026009593\n")


def test_routing_needs_the_aba_checksum():
    assert aba_valid("026009593") and not aba_valid("026009594")
    assert find_routing("New routing 026009593, thanks") == ["026009593"]
    assert find_routing("New routing 026009594, thanks") == []
    assert find_routing("ref 0260095931") == []                        # ten digits is not a routing number
    assert find_routing("account 000 026009593") == []                 # nine digits inside a grouped account
    assert find_routing("ABA: 026009593\nAcct: 0001 2345 4821") == ["026009593"]


def test_a_comma_after_a_phone_is_not_an_extension():
    assert find_routing("Contact: 212-555-0199, 026009593") == ["026009593"]


def test_an_extension_is_never_a_routing_number():
    text = "Call 212-555-0199 ext. 123454821"                                         # nine digits, valid ABA checksum
    assert find_routing(text) == [] and ledger_text(text) == "Call (212) 555-0199"


def test_a_loosely_labeled_account_is_not_a_routing_number():
    text = "Our new acct at Evergreen: 026009593"                                      # P9
    assert find_routing(text) == [] and ledger_text(text) == ""
    assert find_routing("Account 1234567, routing 026009593") == ["026009593"]


# Account numbers -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Account: 0001 2345 4821",
    "Account number 0001-2345-4821 from today",
    "Acct #: 000123454821",
    "Routing 026009593\nAccount Number\n0001\n2345\n4821\n\nThanks",
    "our new acct no. is 0001\u20112345\u20114821.",                   # non-breaking hyphens
    "Account 0001\u202a2345\u202c4821",                                 # bidi controls (final review I2b)
    "Account 0001\u061c2345\u202e4821",
])
def test_accounts_are_found_grouped_or_labeled(text):
    assert find_accounts(text) == [ACCOUNT]


def test_accounts_need_a_label_and_four_to_seventeen_digits():
    assert find_accounts("please pay 000123454821 from today") == []
    assert find_accounts("Account: 123") == []
    assert find_accounts("Account: 123456789012345678") == []
    assert find_accounts("For account questions, call 212-555-0199.") == []
    assert find_accounts("Call your account manager at 212-555-0199.") == []
    assert find_accounts("Pay the account ending 4821.") == []          # a last-four reference is not an account
    assert find_accounts("Account: \uff10\uff10\uff10\uff11\uff12\uff13\uff14\uff15\uff14\uff18\uff12\uff11") == [ACCOUNT]


def test_a_last_four_reference_is_not_an_account():
    assert find_accounts("the account ending 8888 is closed; new account 000088889999") == ["000088889999"]
    assert find_accounts("Acct last four: 4821") == []


def test_every_text_function_reads_only_the_first_50000_characters():
    pad = "x" * 50_000
    assert rules.MAX_CHARS == 50_000
    assert find_accounts(pad + " Account: 000123454821") == [] and find_routing(pad + " ABA 026009593") == []
    assert ledger_text(pad + " Call 212-555-0199") == "" and rules.redact_for_model(pad + " 1234") == pad
    assert not flag("", pad + " We have changed our bank.").flagged


# The ledger's text ---------------------------------------------------------------------------------
#
# The ledger never ingests the email's text, masked or not. ledger_text() rebuilds one line per phone the
# keep-decision keeps: the words of the original line before that phone (nothing numeric, whitespace
# collapsed, the last 80 characters), a space, and the phone in phonenumbers' national (US) or
# international format. So every stored quote is words plus one canonical phone, by construction.

def test_the_ledger_line_is_the_words_before_the_phone_and_the_phone_in_canonical_form():
    assert ledger_text("Call 212-555-0199") == "Call (212) 555-0199"
    assert ledger_text("Call  \t 2125550199 now, or 617.555.0143.") == "Call (212) 555-0199\nCall now or (617) 555-0143"
    assert ledger_text("Ref 12 ab3c: 212(555)0199") == "Ref abc: (212) 555-0199"
    # Only letters, colons and apostrophes stay: brackets and plus signs change how the matcher reads the phone.
    assert ledger_text("Jane (AR) | +1 Dana's ext: 212-555-0199") == "Jane AR Dana's ext: (212) 555-0199"
    assert ledger_text("Please pay (9677329484)) 212(555)0199 (+44 20 7946 0958 ext 12345 -") == (
        "Please pay (212) 555-0199\nPlease pay +44 20 7946 0958")                   # seed 105 of the property test
    assert ledger_text("Tel +44 20 7946 0958") == "Tel +44 20 7946 0958"                 # not US: international
    assert ledger_text("Call 416-555-0199") == "Call +1 416-555-0199"                   # Canada
    assert ledger_text("No phone here, account 000123454821.") == ""
    assert ledger_text("212-555-0199") == "(212) 555-0199"


def test_the_words_before_the_phone_are_its_last_80_characters():
    text = "x" * 100 + " AR desk: 212-555-0199"
    words = ("x" * 100 + " AR desk:")[-80:]
    assert len(words) == 80 and ledger_text(text) == words + " (212) 555-0199"


def test_role_words_reach_the_stub_reader():
    assert StubReader().label(ledger_text("AR line: 212-555-0199")) == [
        {"quote": "AR line: (212) 555-0199", "role": "ar", "party": "vendor"}]
    assert ledger_text("Accounts Payable: 212-555-0199") == "Accounts Payable: (212) 555-0199"
    assert ledger_text("Remittance desk Ph: 617-555-0143") == "Remittance desk Ph: (617) 555-0143"


@pytest.mark.parametrize(("text", "expected"), [
    ("Accounts Payable: 212-555-0199", "Accounts Payable: (212) 555-0199"),
    ("Jane Doe | Accounts Receivable | Acme Corp | 617-555-0143", "Jane Doe Accounts Receivable Acme Corp (617) 555-0143"),
    ("Account manager: Dana 212-555-0199", "Account manager: Dana (212) 555-0199"),
    ("Remittance desk Ph: 617-555-0143", "Remittance desk Ph: (617) 555-0143"),
])
def test_signature_lines_keep_their_phones(text, expected):
    assert ledger_text(text) == expected


def test_a_phone_on_a_line_with_a_phone_word_or_no_payment_word_is_kept():
    assert ledger_text("Our office: 212-555-0199") == "Our office: (212) 555-0199"
    assert ledger_text("Questions about the wire? Call 212-555-0199.") == "Questions about the wire Call (212) 555-0199"
    assert ledger_text("Call 212-555-0199, then wire it to 987654321098 today") == "Call (212) 555-0199"


@pytest.mark.parametrize("text", [
    "Please wire to 7783110245 at Evergreen Bank.",                                # P1
    "Account at Chase: 2299100477",                                                # P2
    "Send the ACH to 12125550199 tomorrow.",                                       # P5
    "Account: 212-555-0199",
])
def test_a_phone_shaped_number_on_a_payment_line_without_a_phone_word_is_not_kept(text):
    assert ledger_text(text) == ""


@pytest.mark.parametrize("text", [
    "Account: 7783110245.\nTo confirm, wire to 7783110245 today.",
    "Account: 7783110245.\nTo confirm, it is 7783110245.",                         # no payment word on line 2
    "Account: 778 311 0245\nTo confirm, it is (778) 311-0245.",                    # grouped differently
    "Account: 026009593\nTo confirm, it is 026009593.",                            # nine digits, valid ABA checksum
])
def test_a_phone_that_repeats_a_found_account_is_not_kept(text):
    assert ledger_text(text) == "" and find_routing(text) == []


@pytest.mark.parametrize("text", [
    "Account: 987654321098\nCall 765-432-1098",                                    # the review's inputs (I3)
    "Account: 987654321098 / Call 765-432-1098",
    "Routing 026009593 Account 4155550199123\nCall us at (415) 555-0199",
    "Routing 026009593 Account 4155550199123 / Call us at (415) 555-0199",
    "Account: 1 765 432 1098 55\nCall +1 (765) 432-1098",                           # grouped, with the country code
    "Account 0007783110245\nCall 778-311-0245",                                    # a repeat without its leading zeros
])
def test_a_phone_inside_a_found_account_is_not_kept(text):
    """The partial-account residual (final review I3): a phone whose national digits sit inside a found
    account would put most of the account in the append-only ledger, and with the row's last four all of it."""
    assert ledger_text(text) == ""
    assert not any(numerics(quote) for quote, _ in stored(text))


def test_a_phone_inside_an_account_found_in_another_field_is_not_kept():
    assert ledger_text("Call 765-432-1098", also_mask=["987654321098"]) == ""
    assert ledger_text("Call 765-432-1098", also_mask=["0007654321098"]) == ""


def test_a_phone_beside_an_account_it_is_not_part_of_is_kept():
    # The control: the new rule must not drop a phone that only sits beside an account.
    assert ledger_text("Account: 987654321098\nCall 212-555-0199") == "Call (212) 555-0199"
    assert ledger_text("Call 212-555-0199", also_mask=["987654321098"]) == "Call (212) 555-0199"


def test_accounts_found_in_another_field_are_not_kept():
    # Task 8 may pass the body's accounts when it builds the subject's ledger text.
    assert ledger_text("Re: call 7783110245", also_mask=["7783110245"]) == ""
    assert ledger_text("Re: 778-311-0245 is ours", also_mask=["7783110245"]) == ""
    assert ledger_text("Re: call 7783110245") == "Re: call +1 778-311-0245"


@pytest.mark.parametrize("text", [
    "Call 212-555-0199 ext. 000123454821",
    "Call 212-555-0199 ext. 4821",
    "Call 212-555-0199 x000123454821",
    "Call 212-555-0199 #000123454821",
    "Call 212-555-0199x4821 now",                                                  # glued on: kept, the extension dropped
])
def test_an_extension_is_never_part_of_the_phone(text):
    assert ledger_text(text) == "Call (212) 555-0199"


@pytest.mark.parametrize(("text", "expected"), [
    ("Tel +44 (0)20 7946 0958 ext 000123454821", "Tel +44 20 7946 0958"),
    ("Call 011 44 20 7946 0958 ext. 000123454821", "Call +44 20 7946 0958"),
])
def test_a_prefixed_phone_keeps_only_its_number(text, expected):
    assert ledger_text(text) == expected


@pytest.mark.parametrize("text", [
    "Please wire the payment to the account below:\n7783110245\nEvergreen Bank",
    "Our new account is below\n7783110245",
    "Our new account is below\r\n7783110245",
    "Our new account is below\r7783110245",
    "Our new account is below\u20287783110245",
    "Please wire the payment to the account below:\n\n(778) 311-0245",
])
def test_a_number_alone_on_a_line_takes_the_words_of_the_line_before(text):
    assert ledger_text(text) == ""


def test_a_phone_alone_on_a_line_after_a_phone_word_is_kept():
    assert ledger_text("Please wire the payment, and if anything is unclear call me on\n212-555-0199") == "(212) 555-0199"


@pytest.mark.parametrize("text", [
    "Call 212-555-0199. Account: 7783|1102|45",
    "Account: 0001  --  2345  --  4821, call 212-555-0199",
    "Call 212-555-0199 or 778\u00b7311\u00b70245",
    "Call 212-555-0199 or \u4e03\u4e03\u516b\u4e09\u4e00\u4e00\u3007\u4e8c\u56db\u4e94",   # CJK numerals
])
def test_nothing_else_on_a_phone_line_reaches_the_ledger(text):
    out = ledger_text(text)
    assert [quote for quote, _ in stored(text)] == [out] and parse_phone(out) == ["+12125550199"]
    assert numerics(out) == "2125550199"


# Repros from every fix round: lines where masking the email's text in place let the ledger store an
# account, or a phone-shaped account, next to a phone, or stored a line masking had turned into a phone line.

REPROS = [
    "Ref 212(555)0199(77777) Our new account: 7783|1102|45",                          # round 4: masking made a phone
    "Ph 212(555)0199(77777), acct 778\u00b7311\u00b70245",
    "Call 2125550199)\u4e03\u4e03\u516b\u4e09\u4e00\u4e00\u3007\u4e8c\u56db\u4e94x",  # round 4: CJK beside a phone
    "Ref 212-555-0199, 2125550199x\u4e03\u4e03\u516b\u4e09\u4e00\u4e00\u3007\u4e8c\u56db\u4e94 21569.",
    "Ph 212-555-0199, 77777/(617) 555-0143x51826279-",                                # round 5: a hidden phone
    "Account (call 212-555-0199 to confirm): 7783110245x1",                           # round 5: the account stored
    "Call 212-555-0199, then wire it to 987654321098 today",                           # round 1
    "Call 212-555-0199 987654321098",
    "Account: 7783110245.\nTo confirm, wire to 7783110245 today.",
    "Please wire to 7783110245 at Evergreen Bank.",
    "Call 212-555-0199 ext. 000123454821",
    "Tel +44 (0)20 7946 0958 ext 000123454821",                                        # round 2
    "Call 011 44 20 7946 0958 ext. 000123454821",
    "Please wire the payment to the account below:\n\n(778) 311-0245",
    "Call 212-555-0199. Account: 7783|1102|45",                                        # round 3
    "Account: 0001  --  2345  --  4821, call 212-555-0199",
    "Call 212-555-0199 or 778\u00b7311\u00b70245",
    "Contact: 212-555-0199, 026009593",
    "Call 212-555-0199 x4821, acct 000123454821, routing 026009593",
]


@pytest.mark.parametrize("text", REPROS)
def test_every_stored_quote_is_words_and_one_phone(text):
    for quote, phone in stored(text):
        assert numerics(quote) in digits_of(phone), quote
        assert numeric_runs_outside_the_phones(quote) == []
    for account in find_accounts(text):
        assert surviving_account_digits(ledger_text(text), account) == set()


def test_the_round_4_repros_hold_no_phone_so_the_ledger_gets_nothing():
    for text in REPROS[:2]:
        assert parse_phone(text) == [] and ledger_text(text) == ""


# A property test over generated text: every format the rounds found (212(555)0199, "(digits)" glue,
# x/ext/# extensions, ~ | and middle-dot joins, phone-shaped labeled and unlabeled accounts, CJK and
# fullwidth numerals, several phones per line). The generator is shown to produce the bug class first.

PHONE_FORMS = ["212-555-0199", "(617) 555-0143", "+44 20 7946 0958", "617.555.0143", "2125550199", "212(555)0199",
               "212 555 0199", "+1 212 555 0199", "416-555-0199"]
EXTENSIONS = ["", "", "", " x51826279", "x4821", " ext. 000123454821", " ext 12345", "#77777", " #1234"]
ACCOUNT_FORMS = ["7783110245", "2299100477", "778-311-0245", "(778) 311-0245", "7783|1102|45", "778\u00b7311\u00b70245",
                 "0001  --  2345  --  4821", "000123454821", "\u4e03\u4e03\u516b\u4e09\u4e00\u4e00\u3007\u4e8c\u56db\u4e94",
                 "\uff17\uff17\uff18\uff13\uff11\uff11\uff10\uff12\uff14\uff15", "026009593", "987654321098", "77777", "51826279"]
LABELS = ["Account:", "Acct #", "Our new account:", "Account (call us to confirm):", "a/c", "Account number is"]
JOINS = [" ", ", ", "~", "|", "\u00b7", "/", "", " - ", ") ", " ("]
LINE_WORDS = ["Ref", "Ph", "Call", "Tel", "Wire to", "AR line:", "Accounts Payable:", "Remittance desk Ph:",
              "Please pay", "Bank:", "Account:", "acct", ""]


def generated(seed: int) -> str:
    rng = random.Random(seed)
    lines = []
    for _ in range(rng.randint(1, 3)):
        parts = []
        for _ in range(rng.randint(1, 4)):
            kind = rng.random()
            if kind < 0.45:
                parts.append(rng.choice(PHONE_FORMS) + rng.choice(EXTENSIONS))
            elif kind < 0.65:
                parts.append(rng.choice(LABELS) + " " + rng.choice(ACCOUNT_FORMS))
            elif kind < 0.85:
                parts.append(rng.choice(ACCOUNT_FORMS))
            elif kind < 0.95:
                parts.append("(" + "".join(rng.choice("0123456789") for _ in range(rng.randint(4, 11))) + ")")
            else:
                parts.append("".join(rng.choice("0123456789") for _ in range(rng.randint(5, 12))))
        lines.append((rng.choice(LINE_WORDS) + " " + "".join(p + rng.choice(JOINS) for p in parts)).strip())
    return "\n".join(lines)


SEEDS = range(200)


def leaks_in(text: str) -> list[tuple[str, str]]:
    """Stored quotes from `text` as the stub reads it that carry a numeric run outside their phone, or whose
    phone is a labeled account."""
    accounts = {a for a in find_accounts(text) if len(a) >= 5}
    out = []
    for label in StubReader().label(text):
        phone = parse_phone(label["quote"])[0]
        if numeric_runs_outside_the_phones(label["quote"]) or any(a in phone for a in accounts):
            out.append((label["quote"], phone))
    return out


def test_the_generator_produces_the_bug_class_on_the_original_text():
    leaks = [leak for seed in SEEDS for leak in leaks_in(generated(seed))]
    assert len(leaks) >= 20, leaks[:5]


@pytest.mark.parametrize("seed", SEEDS)
def test_every_stored_line_is_words_and_one_kept_phone(seed):
    text = generated(seed)
    out = ledger_text(text)
    labels = StubReader().label(out)
    kept = [e164 for _, _, e164 in rules._pieces(text)[1]]
    assert len(labels) == (out.count("\n") + 1 if out else 0)                 # every rebuilt line is stored
    assert [parse_phone(label["quote"])[0] for label in labels] == kept         # as the phone the keep-decision kept
    on_the_lines = phones_on_the_lines_of(text)
    accounts = {a for a in find_accounts(text) if len(a) >= 5}
    for label in labels:
        quote, phone = label["quote"], parse_phone(label["quote"])[0]
        assert phone in on_the_lines                                           # a phone of the original, never made up
        assert numerics(quote) in digits_of(phone), quote                      # nothing numeric but the phone
        assert not any(a in numerics(quote) for a in accounts), quote


def test_the_property_test_runs_in_a_few_seconds():
    start = time.perf_counter()
    for seed in SEEDS:
        ledger_text(generated(seed))
    assert time.perf_counter() - start < 3.0


def best_of_five(function, argument) -> float:
    """The fastest of five runs, in seconds. The minimum is used because it measures the code: a slow run is
    machine load (another process, a busy CPU), and any one fast run shows the code can be that fast."""
    times = []
    for _ in range(5):
        start = time.perf_counter()
        function(argument)
        times.append(time.perf_counter() - start)
    return min(times)


def test_the_phone_scan_is_fast_on_one_long_line_of_phones():
    line = ",".join(["212-555-0199"] * 3846)                                      # 50 KB
    assert len(line) > 49_000
    for function in (ledger_text, find_routing):
        assert best_of_five(function, line) < 0.3, function.__name__


def test_the_phone_scan_is_fast_on_many_phone_lines():
    text = "AR line: 212-555-0199\n" * 2270                                      # 50 KB, under MAX_CHARS
    assert len(text) > 49_000
    assert best_of_five(ledger_text, text) < 0.3
    assert ledger_text(text).count("\n") == 2269


def test_ledger_text_is_fast_on_a_long_email():
    # Many found accounts, and many runs that repeat none of them: each run is checked against every account.
    text = "".join(f"Account: {7783110000 + i}. Wire {5000000000 + i} or {6000000000 + i}.\n" for i in range(8300))
    assert len(text) > 431_000
    start = time.perf_counter()
    ledger_text(text)
    assert time.perf_counter() - start < 1.0


def test_find_accounts_reads_past_the_cap_only_when_asked():
    text = "x" * rules.MAX_CHARS + " Our other account 7783110245"
    assert find_accounts(text) == []
    assert find_accounts(text, limit=1_000_000) == ["7783110245"]
    assert find_accounts(text, limit=10) == []


@pytest.mark.parametrize("text", [
    "account " + " " * 999_992,                               # one label, then a long gap and no number
    ("account no " * 100_000)[:1_000_000],                    # label words only
    ("acct 1 " * 150_000)[:1_000_000],                        # numbers too short to be accounts
    "Account " + "1 " * 499_996,                              # one run of half a million digits
    ("account number is the new " * 40_000)[:1_000_000],      # every word a label word
    "acct " + "-" * 999_995,                                  # separators only
    ("acct:" * 200_000)[:1_000_000],
    "".join(f"Acct {10_000_000 + i} " for i in range(72_000))[:1_000_000],   # tens of thousands of accounts
], ids=["gap", "label-words", "short-runs", "one-long-run", "all-label-words", "separators", "colons", "many-accounts"])
def test_find_accounts_is_fast_on_a_million_characters(text):
    assert len(text) >= 999_000
    start = time.perf_counter()
    find_accounts(text, limit=1_000_000)
    assert time.perf_counter() - start < 1.0


# The model's copy ----------------------------------------------------------------------------------

# P3 was not spelled out in the review message; these two stand in for it: an unlabeled valid routing
# number and a plain phone, both of which the ledger may see and the model must not.
MODEL_PROBES = [
    "Please wire to 7783110245 at Evergreen Bank.",                                  # P1
    "Account at Chase: 2299100477",                                                  # P2
    "Send it to 026009593 at Evergreen.",                                            # P3 stand-in
    "Call Dana at 212-555-0199 with questions.",                                     # P3 stand-in
    "Send the ACH to 12125550199 tomorrow.",                                         # P5
    "Call 212-555-0199 ext. 000123454821",                                           # the extension case
    "Our new acct at Evergreen: 026009593",                                          # P9
    "Account: 7783110245.\nTo confirm, it is 7783110245.",                           # the repeat
]


@pytest.mark.parametrize("text", MODEL_PROBES)
def test_the_model_sees_no_run_of_five_digits(text):
    out = rules.redact_for_model(text)
    assert [m.group(0) for m in DIGIT_RUN.finditer(out) if sum(c.isdigit() for c in m.group(0)) >= 5] == []


MODEL_NO_DIGIT_PROBES = [
    "Account: 000123454821",
    "0001  --  2345  --  4821",                                                     # gaps longer than masking.py groups
    "7783|1102|45",
    "778\u00b7311\u00b70245",                                                         # middle dots
    "".join(d + "\ufe0f\u20e3" for d in "7783110245"),                              # keycaps
    "\u2077\u2077\u2078\u00b3\u00b9\u00b9\u2070\u00b2\u2074\u2075",                          # superscripts
    "\uff17\uff17\uff18\uff13\uff11\uff11\uff10\uff12\uff14\uff15",                          # fullwidth
    "\u0667\u0667\u0668\u0663\u0661\u0661\u0660\u0662\u0664\u0665",                          # Arabic-Indic
    "Call 212-555-0199 or wire 026009593.",
]


def test_the_model_copy_is_cut_to_50000_characters_after_normalizing():
    assert len(rules.redact_for_model("\ufdfa" * 50_000)) == 50_000


@pytest.mark.parametrize("text", MODEL_NO_DIGIT_PROBES)
def test_the_model_copy_has_no_digit_at_all(text):
    out = rules.redact_for_model(text)
    assert not any(c.isdecimal() or c.isdigit() for c in out)
    assert not any(c.isdecimal() for c in unicodedata.normalize("NFKC", out))


# Fingerprint ---------------------------------------------------------------------------------------

def test_fingerprint_matches_adr8_material():
    key = b"fingerprint-key-for-tests-only-0123456789"
    expected = hmac.new(key, b"026009593|000123454821", hashlib.sha256).hexdigest()
    assert requested_fingerprint(key, "026009593", ACCOUNT) == expected
    assert requested_fingerprint(key, "026009593", "000123454822") != expected
    assert requested_fingerprint(b"another-key", "026009593", ACCOUNT) != expected
