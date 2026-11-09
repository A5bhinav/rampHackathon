"""The fixed text and the question order (R8, R4)."""
import re

from dialback_policy.guard import GuardContext, check
from dialback_policy.script import FALLBACK_LINE, ORDER, PHASE_SLOT, QUESTIONS, STILL_THERE, Phase, Script

SCRIPT = Script(customer_name="Fictional Co", callback_number="+1 415 555 0100")


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text)[0]


def test_first_sentence_discloses_ai_and_recording() -> None:
    first = first_sentence(SCRIPT.disclosure())
    assert "AI" in first and "recorded" in first and "Fictional Co" in first


def test_a_customer_name_with_punctuation_cannot_cut_the_disclosure_short() -> None:
    for name in ("J. Crew Group", "Yahoo! Inc", "Dr. Pepper Co."):
        script = Script(customer_name=name, callback_number="+1 415 555 0100")
        for line in (script.disclosure(), script.voicemail()):
            first = first_sentence(line)
            assert "AI" in first and "recorded" in first, line


def test_voicemail_discloses_ai_and_recording_too() -> None:
    first = first_sentence(SCRIPT.voicemail())
    assert "AI" in first and "recorded" in first


def test_disclosure_gives_a_callback_number() -> None:
    assert "+1 415 555 0100" in SCRIPT.disclosure()


def test_question_order_is_fixed() -> None:
    # Name and role are two questions, each answer recorded as said (spec 0034, B5 and D2).
    assert ORDER == (Phase.CONSENT, Phase.COMPANY, Phase.DEPARTMENT, Phase.NAME, Phase.ROLE, Phase.CHANGE,
                     Phase.LAST4)
    assert [PHASE_SLOT[p] for p in ORDER] == ["recording_consent", "company_named", "department", "speaker_name",
                                             "speaker_role", "change_requested", "account_last4"]
    assert set(QUESTIONS) == set(ORDER)


def test_name_and_role_have_their_own_questions() -> None:
    assert QUESTIONS[Phase.NAME].wording(SCRIPT) == "May I have your name?"
    assert QUESTIONS[Phase.ROLE].wording(SCRIPT) == "And what's your role there?"


def test_company_question_never_names_the_vendor() -> None:
    assert QUESTIONS[Phase.COMPANY].wording(SCRIPT) == "Could you tell me the name of the company I've reached?"


def test_every_fixed_line_passes_the_guard() -> None:
    ctx = GuardContext(vendor_names=("Acme Fasteners LLC",), callback_number=SCRIPT.callback_number,
                       customer_name=SCRIPT.customer_name)
    lines = [SCRIPT.disclosure(), SCRIPT.consent_question(), SCRIPT.voicemail(), SCRIPT.close(),
             SCRIPT.consent_refused(), SCRIPT.callback_close(), SCRIPT.goodbye(), FALLBACK_LINE, STILL_THERE]
    lines += [QUESTIONS[p].wording(SCRIPT) for p in ORDER]
    for line in lines:
        assert check(line, ctx).ok, line


def test_voicemail_says_nothing_about_banks_changes_or_amounts() -> None:
    message = SCRIPT.voicemail().lower().replace("accounts payable", "")
    assert not any(word in message for word in ("bank", "account", "change", "payment", "amount", "invoice"))
