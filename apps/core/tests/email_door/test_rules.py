"""Flagging, bank-detail extraction and redaction for the email door (spec 0086, Task 6)."""
from __future__ import annotations

import hashlib
import hmac
import re

import pytest
from dialback_core.bank import aba_valid
from dialback_core.email_door.masking import mask_digit_runs
from dialback_core.email_door.rules import (
    Flag,
    find_accounts,
    find_routing,
    flag,
    redact,
    requested_fingerprint,
)
from dialback_ledger.candidates import parse_phone

ACCOUNT = "000123454821"


def five_digit_pieces_of(account: str) -> set[str]:
    return {account[i:i + 5] for i in range(len(account) - 4)}


def surviving_account_digits(redacted: str, account: str = ACCOUNT) -> set[str]:
    """Every five-digit stretch of `account` that can still be read in `redacted`, separators ignored."""
    found = set()
    for m in re.finditer(r"\d(?:[^\d*]{0,5}\d)*", redacted):
        digits = re.sub(r"\D", "", m.group(0))
        found |= {digits[i:i + 5] for i in range(len(digits) - 4)} & five_digit_pieces_of(account)
    return found


# Flagging ------------------------------------------------------------------------------------------

def test_flag_needs_change_words_near_payment_words():
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
])
def test_flag_examples(flagged, subject, text):
    assert flag(subject, text).flagged is flagged


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

def test_routing_needs_the_aba_checksum():
    assert aba_valid("026009593") and not aba_valid("026009594")
    assert find_routing("New routing 026009593, thanks") == ["026009593"]
    assert find_routing("New routing 026009594, thanks") == []
    assert find_routing("ref 0260095931") == []                        # ten digits is not a routing number
    assert find_routing("account 000 026009593") == []                 # nine digits inside a grouped account
    assert find_routing("ABA: 026009593\nAcct: 0001 2345 4821") == ["026009593"]


# Account numbers -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Account: 0001 2345 4821",
    "Account number 0001-2345-4821 from today",
    "Acct #: 000123454821",
    "Routing 026009593\nAccount Number\n0001\n2345\n4821\n\nThanks",
    "our new acct no. is 0001\u20112345\u20114821.",                   # non-breaking hyphens
])
def test_accounts_are_found_grouped_or_labeled(text):
    assert find_accounts(text) == [ACCOUNT]


def test_accounts_need_a_label_and_four_to_seventeen_digits():
    assert find_accounts("please pay 000123454821 from today") == []
    assert find_accounts("Account: 123") == []
    assert find_accounts("Account: 123456789012345678") == []
    assert find_accounts("For account questions, call 212-555-0199.") == []
    assert find_accounts("Call your account manager at 212-555-0199.") == []
    assert find_accounts("Pay the account ending 4821.") == ["4821"]
    assert find_accounts("Account: \uff10\uff10\uff10\uff11\uff12\uff13\uff14\uff15\uff14\uff18\uff12\uff11") == [ACCOUNT]


# Redaction -----------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ("Account: 0001 2345 4821", "Account: **** **** 4821"),
    ("Account number 0001-2345-4821 from today", "Account number ****-****-4821 from today"),
    ("Acct #: 000123454821", "Acct #: ********4821"),
    ("Account Number\n0001\n2345\n4821\n", "Account Number\n****\n****\n4821\n"),
])
def test_redact_leaves_only_the_last_four(text, expected):
    out = redact(text)
    assert out == expected
    assert surviving_account_digits(out) == set()
    assert "4821" in out


def test_redact_keeps_phone_and_routing():
    text = ("New routing 026009593, account 0001-2345-4821.\n"
            "AR direct: (617) 555-0143\nCall Dana at 212-555-0199 with questions.")
    out = redact(text)
    assert "026009593" in out and find_routing(out) == ["026009593"]
    assert parse_phone(out) == ["+16175550143", "+12125550199"]
    assert "****-****-4821" in out and surviving_account_digits(out) == set()


def test_redact_masks_an_unlabeled_number_on_a_phone_line():
    # The ledger's stub reader stores a phone's whole line, so an unlabeled number there must be masked too.
    line = "Call 212-555-0199, then wire it to 987654321098 today"
    out = redact(line)
    assert parse_phone(out) == ["+12125550199"]
    assert "987654321098" not in out and "********1098" in out
    assert surviving_account_digits(out, "987654321098") == set()
    # Run together with the phone by a single space, the number is still masked and the phone still parses.
    out = redact("Call 212-555-0199 987654321098")
    assert parse_phone(out) == ["+12125550199"] and surviving_account_digits(out, "987654321098") == set()


def test_redact_keeps_a_valid_routing_number_and_masks_an_invalid_one():
    assert redact("ABA 026009593") == "ABA 026009593"
    assert redact("ABA 026009594") == "ABA *****9594"


def test_a_labeled_account_is_masked_even_when_it_looks_like_a_phone_or_a_routing_number():
    assert redact("Account: 212-555-0199") == "Account: ***-***-0199"
    assert redact("Account: 026009593") == "Account: *****9593"


def test_redact_masks_at_least_what_masking_masks():
    # Every digit that mask_digit_runs hides stays hidden, except phones and routing numbers.
    text = "Inv 2291 for 18,450.00. Wire 5530 7781 02 or 44100-99231 by Friday."
    assert redact(text) == mask_digit_runs(text)


# Fingerprint ---------------------------------------------------------------------------------------

def test_fingerprint_matches_adr8_material():
    key = b"fingerprint-key-for-tests-only-0123456789"
    expected = hmac.new(key, b"026009593|000123454821", hashlib.sha256).hexdigest()
    assert requested_fingerprint(key, "026009593", ACCOUNT) == expected
    assert requested_fingerprint(key, "026009593", "000123454822") != expected
    assert requested_fingerprint(b"another-key", "026009593", ACCOUNT) != expected
