from __future__ import annotations

import time
from uuid import UUID, uuid4

from dialback_core.email_door.match import Match, VendorRef, match_vendor, name_in_text

ACME = VendorRef(uuid4(), "Acme Fasteners LLC", (), ("acme.example",))
BRIGHT = VendorRef(uuid4(), "Brightline Office Supply Inc", (), ("brightline.example",))
COBALT = VendorRef(uuid4(), "Cobalt Freight LLC", ("Cobalt Logistics",), ("cobalt.example",))
VENDORS = [ACME, BRIGHT, COBALT]
BODY = "Please change our bank details."


def test_vendor_domain_matches_without_red_flags():
    m = match_vendor("Dana <ar@acme.example>", "New bank", BODY, VENDORS)
    assert m == Match(ACME.vendor_id, "domain", ())


def test_lookalike_domain_matches_with_red_flag():
    m = match_vendor("ar@acrne.example", "New bank", BODY, VENDORS)
    assert m == Match(ACME.vendor_id, "lookalike", ("lookalike_domain",))


def test_gmail_naming_acme_fasteners_matches_off_domain():
    m = match_vendor("someone@gmail.com", "Acme Fasteners bank change", BODY, VENDORS)
    assert m == Match(ACME.vendor_id, "name", ("sender_off_domain",))
    m = match_vendor("someone@gmail.com", "hi", "Acme Fasteners has a new account.", VENDORS)
    assert m.vendor_id == ACME.vendor_id and m.how == "name"


def test_acme_alone_does_not_match():
    m = match_vendor("someone@gmail.com", "Acme", "Acme has a new bank.", VENDORS)
    assert m == Match(None, "none", ())


def test_name_words_out_of_order_do_not_match():
    assert not name_in_text("Acme Fasteners LLC", "Fasteners Acme changed banks")
    assert name_in_text("Acme Fasteners", "Acme and Fasteners")  # ruling: "and" is dropped from the text too
    assert name_in_text("Acme Fasteners LLC", "from Acme Fasteners, Inc.")


def test_accents_and_smart_quotes_still_match():
    assert name_in_text("Acme Fasteners LLC", "ACME FASTENERS\u2019")
    assert name_in_text("Acme Fasteners LLC", "Acm\u00e9 Fasteners, L.L.C.")
    assert name_in_text("Joe\u2019s Diner", "Joe's Diner moved")
    # ruling: "and" and "the" are dropped from the text too; other suffix words only from the name
    assert name_in_text("Smith & Sons", "Smith Sons")
    assert name_in_text("Smith & Sons", "Smith and Sons")
    assert name_in_text("The Smith Sons", "Smith and Sons")
    assert not name_in_text("Acme Fasteners", "Acme Co Fasteners")


def test_two_vendors_named_is_several():
    m = match_vendor("x@gmail.com", "Acme Fasteners and Cobalt Freight", BODY, VENDORS)
    assert m == Match(None, "several", ())


def test_forwarded_quote_naming_the_vendor_matches():
    text = "FYI\n\n> On Mon, Dana wrote:\n> Acme Fasteners is changing banks.\n> Routing 026009593"
    m = match_vendor("boss@gmail.com", "Fwd: payment", text, VENDORS)
    assert m == Match(ACME.vendor_id, "name", ("sender_off_domain",))


def test_trade_name_matches():
    m = match_vendor("x@gmail.com", "Cobalt Logistics", BODY, VENDORS)
    assert m.vendor_id == COBALT.vendor_id and m.how == "name"


def test_first_matching_step_decides():
    # domain match wins even though another vendor is named
    m = match_vendor("ar@acme.example", "Cobalt Freight", BODY, VENDORS)
    assert m == Match(ACME.vendor_id, "domain", ())
    # two vendors share a domain: several at the domain step
    twin = VendorRef(uuid4(), "Twin Co", (), ("acme.example",))
    m = match_vendor("ar@acme.example", "s", BODY, [ACME, twin])
    assert m == Match(None, "several", ())


def test_no_sender_domain_falls_to_name():
    m = match_vendor("not an address", "Acme Fasteners", BODY, VENDORS)
    assert m.how == "name" and m.vendor_id == ACME.vendor_id


def test_suffix_only_name_never_matches():
    v = VendorRef(UUID(int=1), "The Company LLC", (), ())
    assert not name_in_text("The Company LLC", "the company llc")
    assert match_vendor("x@gmail.com", "The Company LLC", "inc llc", [v]).how == "none"


def test_name_past_the_cap_is_ignored_and_matching_is_fast():
    text = "x " * 25_000 + "Acme Fasteners"
    assert not name_in_text("Acme Fasteners", text)
    hostile = "a " * 200_000
    start = time.perf_counter()
    assert not name_in_text("a " * 50 + "b", hostile)
    assert time.perf_counter() - start < 1.0


def test_subdomain_of_a_vendor_domain_is_a_domain_match():
    m = match_vendor("x@Mail.Acme.Example.", "s", BODY, VENDORS)
    assert m == Match(ACME.vendor_id, "domain", ())


def test_vendor_domain_as_a_prefix_of_another_domain_is_not_a_domain_match():
    m = match_vendor("x@acme.example.evil.test", "s", BODY, VENDORS)
    assert m.how != "domain"
    m = match_vendor("x@notacme.example", "s", BODY, VENDORS)
    assert m.how != "domain"
