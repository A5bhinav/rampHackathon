"""The call's fixed text and the order of its questions (design, "How a call runs", steps 3 and 4).

Code owns the order; the model only words the questions. Every line here is fixed text: the
disclosure plays in full before anything else (R8), and nothing here names a bank, an amount,
the new details or what is being checked (R4).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class Phase(StrEnum):
    CONSENT = "consent"
    COMPANY = "company"
    DEPARTMENT = "department"
    NAME_ROLE = "name_role"
    CHANGE = "change"
    LAST4 = "last4"


ORDER: tuple[Phase, ...] = (Phase.CONSENT, Phase.COMPANY, Phase.DEPARTMENT, Phase.NAME_ROLE, Phase.CHANGE,
                            Phase.LAST4)

# The slot whose answer completes each phase. Name and role share a phase; the name completes it.
PHASE_SLOT: dict[Phase, str] = {
    Phase.CONSENT: "recording_consent",
    Phase.COMPANY: "company_named",
    Phase.DEPARTMENT: "department",
    Phase.NAME_ROLE: "speaker_name",
    Phase.CHANGE: "change_requested",
    Phase.LAST4: "account_last4",
}

FALLBACK_LINE = "Sorry, I can't go into that on this call."
STILL_THERE = "Sorry, are you still there?"
REASK = "Sorry, let me ask that again."


@dataclass(frozen=True)
class Script:
    """The fixed lines for one customer. The callback number is the customer's AP line."""
    customer_name: str
    callback_number: str

    def disclosure(self) -> str:
        # AI and recording come before the customer's name, so a name like "J. Crew" can't end the
        # first sentence early (R8).
        return (f"Hi, this is an AI assistant on a recorded line, calling for the accounts payable team at "
                f"{self.customer_name}. If you'd rather call us back, our number is {self.callback_number}.")

    def consent_question(self) -> str:
        return "Is it okay to continue on this recorded line?"

    def voicemail(self) -> str:
        return (f"Hi, this is an AI assistant on a recorded line, calling for the accounts payable team at "
                f"{self.customer_name}. Please call us back at {self.callback_number}. Thank you.")

    def close(self) -> str:
        return "Thank you, that's everything I needed. Have a good day."

    def consent_refused(self) -> str:
        return f"No problem, we won't continue. You can reach us at {self.callback_number}. Goodbye."

    def callback_close(self) -> str:
        return "Thanks, we'll call you back then. Goodbye."

    def goodbye(self) -> str:
        return "Thanks for your time. Goodbye."


@dataclass(frozen=True)
class Question:
    """`goal` tells the model what to ask; `wording` is the fixed fallback when its text can't be used."""
    goal: str
    _wording: Callable[[Script], str]

    def wording(self, script: Script) -> str:
        return self._wording(script)


QUESTIONS: dict[Phase, Question] = {
    Phase.CONSENT: Question("Ask whether it is okay to continue on a recorded line.",
                            lambda s: s.consent_question()),
    Phase.COMPANY: Question("Ask which company you have reached. Never say the vendor's name first.",
                            lambda s: "Could you tell me the name of the company I've reached?"),
    Phase.DEPARTMENT: Question("Ask which department or team you are speaking with.",
                               lambda s: "Which department or team am I speaking with?"),
    Phase.NAME_ROLE: Question("Ask for the person's name and their role.",
                              lambda s: "May I have your name and your role there?"),
    Phase.CHANGE: Question("Ask whether their company recently asked the customer to change how they get paid.",
                           lambda s: f"Has your company recently asked {s.customer_name} to change how you get paid?"),
    Phase.LAST4: Question("Ask for the last four digits of the account they want to be paid into. They may say "
                          "them or type them on the keypad. Never say any digits yourself.",
                          lambda s: "For the account you'd like to be paid into, what are the last four digits? "
                                    "You can say them or enter them on your keypad."),
}
