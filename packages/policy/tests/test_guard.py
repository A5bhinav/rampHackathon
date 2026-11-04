"""The speech guard (R4): every blocked class, the read-back exception, and the fallback."""
import pytest
from dialback_policy.guard import GuardContext, check, guard
from dialback_policy.script import FALLBACK_LINE

CTX = GuardContext(vendor_names=("Acme Fasteners LLC",), callback_number="+1 415 555 0100")


@pytest.mark.parametrize("text", [
    "Your account ends in 4821.",
    "So that's 4 8 2 1?",
    "four eight two one",
    "Routing 021-000-021.",
    "Is it 1,2,3?",
    "Nine, nine, nine.",
])
def test_blocks_runs_of_three_or_more_digits(text: str) -> None:
    verdict = check(text, CTX)
    assert not verdict.ok and "digits" in verdict.reasons


@pytest.mark.parametrize("text", [
    "Could I call you back at 3?",
    "Would 10 tomorrow morning work?",
    "We'll call at 3:30.",
    "Give me one moment.",
])
def test_allows_short_numbers(text: str) -> None:
    assert check(text, CTX).ok


def test_allows_a_read_back_of_digits_the_callee_just_said() -> None:
    ctx = GuardContext(vendor_names=CTX.vendor_names, callee_last_turn="sure, it's 4 8 2 1")
    assert check("Just to make sure I heard you, that was 4821?", ctx).ok
    assert check("Four eight two one, is that right?", ctx).ok


def test_read_back_must_be_exactly_what_the_callee_said() -> None:
    ctx = GuardContext(vendor_names=CTX.vendor_names, callee_last_turn="it's 4821")
    assert not check("Was that 4822?", ctx).ok
    assert not check("And the full number?  0001234821?", ctx).ok


def test_allows_the_ap_callback_number() -> None:
    assert check("You can reach us at 415-555-0100.", CTX).ok
    assert not check("You can reach us at 415-555-0199.", CTX).ok


@pytest.mark.parametrize("text", [
    "Great, you're verified.",
    "That's approved.",
    "Those digits match.",
    "Hmm, that's a mismatch.",
    "Verification is complete.",
])
def test_blocks_status_words(text: str) -> None:
    verdict = check(text, CTX)
    assert not verdict.ok and "status_word" in verdict.reasons


@pytest.mark.parametrize("text", ["$12,500", "about twelve thousand dollars", "the 50 cents fee", "in USD"])
def test_blocks_amounts(text: str) -> None:
    verdict = check(text, CTX)
    assert not verdict.ok and "amount" in verdict.reasons


@pytest.mark.parametrize("text", [
    "Is that your Chase account?",
    "The one at Bank of America?",
    "wells fargo",
    "And your routing number?",
])
def test_blocks_bank_names(text: str) -> None:
    verdict = check(text, CTX)
    assert not verdict.ok and "bank_name" in verdict.reasons


@pytest.mark.parametrize("text", ["Am I speaking with Acme Fasteners?", "Is this ACME?", "acme fasteners, llc"])
def test_blocks_the_vendor_name_until_the_callee_names_the_company(text: str) -> None:
    verdict = check(text, CTX)
    assert not verdict.ok and "vendor_name" in verdict.reasons
    named = GuardContext(vendor_names=CTX.vendor_names, company_named=True)
    assert check(text, named).ok


def test_ordinary_questions_pass() -> None:
    for text in ["Could you tell me the name of the company I've reached?", "Can you confirm your name?",
                 "Which department am I speaking with?", "Thanks, have a good day."]:
        assert check(text, CTX).ok, text


def test_guard_speaks_the_fallback_line_instead() -> None:
    said, verdict = guard("You're verified, the digits match 4821.", CTX)
    assert said == FALLBACK_LINE
    assert set(verdict.reasons) == {"digits", "status_word"}
    said, verdict = guard("Which department am I speaking with?", CTX)
    assert said == "Which department am I speaking with?" and verdict.ok
