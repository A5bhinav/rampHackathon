"""The adversarial text line for the lab (spec 0017, Task 2).

`AdversarialLine` answers a call phase by phase like the voice service's `ScriptedVendor`, but it
can play an injection line at a chosen phase (R9: the caller trying to redirect the record) and it
sets who answered (human | machine). It implements the voice service's `Line` protocol, so the
runner drives it with the real `run_call`. `offer_other_number` is recorded only by the live agent;
the scripted slice tests the offered-number rule through the declared fact instead, so the line
leaves it alone here."""
from __future__ import annotations

import datetime as dt

from dialback_policy.script import Phase
from dialback_policy.step import CalleeSaid, Connected, Event, Hangup, Keypad, Silence

from .scenario import CallerBehavior

_CONSENT_LINE = {"yes": "Yes, that's fine.", "no": "No, I'd rather not."}
_CHANGE_LINE = {"yes": "Yes, we did.", "no": "No, we didn't.", "unsure": "I'm not sure."}


class AdversarialLine:
    """The far end of a lab call: a scripted, possibly hostile caller. Deterministic, so a scenario
    runs the same way every time (the gate is reproducible)."""

    def __init__(self, caller: CallerBehavior) -> None:
        self.caller = caller
        self.heard: tuple[str, ...] = ()

    def connect(self, at: dt.datetime) -> Event:
        return Connected(at, "machine" if self.caller.answered_by == "machine" else "human")

    def hear(self, lines: tuple[str, ...]) -> None:
        self.heard = tuple(lines)

    def next(self, at: dt.datetime, phase: Phase | None) -> Event:
        c = self.caller
        name = phase.name if phase is not None else None
        if c.silent:
            return Silence(at)
        if c.hang_up_at is not None and name == c.hang_up_at:
            return Hangup(at)
        if c.inject is not None and name == c.inject_phase:
            return CalleeSaid(at, c.inject)            # the adversarial turn, in place of the plain answer (R9)
        if phase is Phase.CONSENT:
            return CalleeSaid(at, _CONSENT_LINE[c.consent])
        if phase is Phase.COMPANY:
            return CalleeSaid(at, c.company)
        if phase is Phase.DEPARTMENT:
            return CalleeSaid(at, c.department)
        if phase is Phase.NAME:
            return CalleeSaid(at, c.name)
        if phase is Phase.ROLE:
            return CalleeSaid(at, c.role)
        if phase is Phase.CHANGE:
            return CalleeSaid(at, _CHANGE_LINE[c.change])
        if phase is Phase.LAST4:
            return self._last4(at)
        return CalleeSaid(at, "Okay.")

    def _last4(self, at: dt.datetime) -> Event:
        c = self.caller
        if c.last4_mode == "keypad":
            return Keypad(at, c.last4)
        if c.last4_mode == "speech":
            return CalleeSaid(at, "They're " + " ".join(c.last4) + ".")
        if c.last4_mode == "refuse":
            return CalleeSaid(at, "I'd rather not say.")
        return CalleeSaid(at, "I don't have that handy.")   # dont_know
