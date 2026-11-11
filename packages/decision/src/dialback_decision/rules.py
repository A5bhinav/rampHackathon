"""The rule table (spec 0006). Blocks run in order and the first block with a match decides;
every matching rule in that block is listed:

  earlier denials         -> not_verified   (an earlier call that had consent said "no" or gave
                                              wrong digits; this call's problems can't hide it)
  block 0, input problems  -> needs_review   (before Not verified: answers after a refused or
                                              missing consent aren't evidence)
  block 1, Not verified    -> not_verified   (the hold stays and the controller is alerted)
  block 2, Needs review    -> needs_review
  block 3, Verified        -> verified, only if every condition holds (plus S01 in strict mode)
  otherwise                -> needs_review, naming each condition that wasn't met

Facts that fail validation never reach a rule (NR97). IDs are permanent: a changed rule gets
a new engine version, never a reused number. Each sentence is what the dashboard shows.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .facts import CallChecks, CaseFacts, EarlierCall
from .last_four import LAST_FOUR_RESULTS

CALL_RESULTS = frozenset({"answers_recorded", "no_answer", "voicemail", "busy", "failed", "machine", "declined",
                          "callback_requested"})  # spec 0007: an earlier call may have asked for a callback
CONSENT = frozenset({"yes", "no"})
CHANGE = frozenset({"yes", "no", "unsure"})
FLAGS = frozenset({"pressure_to_approve", "opt_out", "asked_what_is_checked", "asked_to_call_another_number", "other",
                   "speaker_changed"})
ANSWERED_BY = frozenset({"human", "machine"})
SOURCES = frozenset({"call", "manual"})
TRI_STATE = ("company_matches", "consent_first", "answers_in_order", "same_person_yes_and_digits", "dialed_out",
             "dialed_number_is_pick", "line_risk", "speaker_in_change_thread", "offered_number_from_request",
             "speaker_known_before_request")


@dataclass(frozen=True)
class Mode:
    strict: bool
    source: str


@dataclass(frozen=True)
class Rule:
    id: str
    template: str                                # the sentence when it fires (or, for a condition, holds)
    test: Callable[[CaseFacts, Mode], bool]
    unmet: str | None = None                     # a Verified condition's sentence when it doesn't hold
    earlier: Callable[[EarlierCall], bool] | None = None  # Not verified: the same evidence on an earlier call

    @property
    def unmet_id(self) -> str:
        return f"{self.id}_unmet"


# Validation (NR97) ----------------------------------------------------------------------------

def _member(value: object, allowed: frozenset[str] | tuple[str, ...]) -> bool:
    return isinstance(value, str) and value in allowed


def _optional(value: object, allowed: frozenset[str] | tuple[str, ...]) -> bool:
    return value is None or _member(value, allowed)


def _strings(values: object, allowed: frozenset[str] | None = None) -> bool:
    return isinstance(values, tuple) and all(isinstance(v, str) and (allowed is None or v in allowed) for v in values)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _checks_ok(c: object) -> bool:
    if c is None:
        return True
    return type(c) is CallChecks and all(isinstance(getattr(c, n), bool) for n in (
        "disclosure_first", "answers_grounded", "agent_spoke_account_digits", "new_number_accepted")) \
        and _count(c.policy_violations) and _count(c.speech_guard_trips)


def _earlier_ok(call: object) -> bool:
    return type(call) is EarlierCall and _member(call.result, CALL_RESULTS) and _strings(call.recording_consent, CONSENT) \
        and _strings(call.change_requested, CHANGE) and _optional(call.last_four_result, LAST_FOUR_RESULTS) \
        and _strings(call.flags, FLAGS)


def valid(f: object, mode: Mode) -> bool:
    """Every fact has its exact type and an allowed value. Anything else is NR97, never a guess."""
    if type(f) is not CaseFacts or not isinstance(mode.strict, bool) or not _member(mode.source, SOURCES):
        return False
    return (_member(f.call_result, CALL_RESULTS) and isinstance(f.dialed_eligible_number, bool)
            and _strings(f.recording_consent, CONSENT) and _strings(f.company_named)
            and _strings(f.change_requested, CHANGE) and isinstance(f.speaker_named, bool) and _strings(f.speaker_role)
            and _strings(f.flags, FLAGS) and isinstance(f.offered_number, bool) and isinstance(f.digits_typed, bool)
            and _optional(f.last_four_result, LAST_FOUR_RESULTS) and _optional(f.answered_by, ANSWERED_BY)
            and all(getattr(f, n) is None or isinstance(getattr(f, n), bool) for n in TRI_STATE)
            and _checks_ok(f.checks) and isinstance(f.earlier_calls, tuple) and all(map(_earlier_ok, f.earlier_calls))
            and isinstance(f.extra, dict))


# Helpers the rules read ------------------------------------------------------------------------

def _earlier(f: CaseFacts, predicate: Callable[[EarlierCall], bool]) -> bool:
    """An earlier call of this generation counts against the change, never for it."""
    return any(predicate(c) for c in f.earlier_calls)


def consented(c: EarlierCall) -> bool:
    """An earlier call's answers are evidence only if that call had consent."""
    return "yes" in c.recording_consent and "no" not in c.recording_consent


def _denied(c: EarlierCall) -> bool:
    return "no" in c.change_requested or c.last_four_result in ("mismatch", "matches_old_account")


def _only(values: tuple[str, ...], value: str) -> bool:
    return bool(values) and set(values) == {value}


def _conflicting(values: tuple[str, ...]) -> bool:
    return len({" ".join(v.casefold().split()) for v in values}) > 1


def _earlier_concern(c: EarlierCall) -> bool:
    """Anything an earlier call raised short of a consented denial (which is Not verified)."""
    return bool(c.flags) or "unsure" in c.change_requested or _conflicting(c.change_requested) \
        or c.last_four_result in ("near_miss", "ambiguous", "inconsistent") or "no" in c.recording_consent \
        or (_denied(c) and not consented(c))


def _clean_script(c: CallChecks | None) -> bool:
    """Computed from the fields, so nothing can override it."""
    return c is not None and c.disclosure_first is True and c.agent_spoke_account_digits is False \
        and c.new_number_accepted is False and c.policy_violations == 0 and c.speech_guard_trips == 0


# Block 0: input problems ------------------------------------------------------------------------

INVALID = "NR97_facts_invalid"
ENGINE_ERROR = "NR98_engine_error"
NOT_MET = "NR99_conditions_not_met"

BLOCK0: tuple[Rule, ...] = (
    Rule("NR01_no_live_person", "No person answered the questions on this call.",
         lambda f, m: f.call_result != "answers_recorded" or f.answered_by == "machine"),
    Rule("NR02_no_recording_consent", "Recording consent was refused, contradicted, or not the first thing the person answered.",
         lambda f, m: "no" in f.recording_consent or f.consent_first is False),
)

# Block 1: Not verified --------------------------------------------------------------------------

NOT_VERIFIED: tuple[Rule, ...] = (
    Rule("NV01_vendor_denied_change", "The person said the vendor did not ask to change its bank details.",
         lambda f, m: "no" in f.change_requested, earlier=lambda c: "no" in c.change_requested),
    Rule("NV02_digits_mismatch", "The digits the person gave do not match the new account.",
         lambda f, m: f.last_four_result == "mismatch", earlier=lambda c: c.last_four_result == "mismatch"),
    Rule("NV03_digits_match_old_account", "The digits the person gave match the old account, not the new one.",
         lambda f, m: f.last_four_result == "matches_old_account",
         earlier=lambda c: c.last_four_result == "matches_old_account"),
    Rule("NV04_offered_number_from_request", "The person offered a phone number that first appeared in the change request.",
         lambda f, m: f.offered_number_from_request is True),
)

# Block 2: Needs review --------------------------------------------------------------------------

NEEDS_REVIEW: tuple[Rule, ...] = (
    # Spec 0082, D7b: company_matches is False only for an answer with a refusal word (D7a). The sentence is
    # true under both meanings NR03 has had, so a decision-1.0.0 NR03 (no match) still reads correctly.
    Rule("NR03_wrong_company",
         "The company answer did not name this vendor, or said the person is not with it or only works with it.",
         lambda f, m: f.company_matches is False),
    Rule("NR04_speaker_from_change_thread", "The person first appeared in the change request thread.",
         lambda f, m: f.speaker_in_change_thread is True),
    Rule("NR05_digits_near_miss", "The digits the person gave are one digit off the new account.",
         lambda f, m: f.last_four_result == "near_miss"),
    Rule("NR06_digits_not_provided", "The person did not give four digits.", lambda f, m: f.last_four_result == "not_provided"),
    Rule("NR07_digits_ambiguous", "The old and new accounts end in the same four digits, so the digits prove nothing.",
         lambda f, m: f.last_four_result == "ambiguous"),
    Rule("NR08_digits_inconsistent", "The person gave two different sets of digits.",
         lambda f, m: f.last_four_result == "inconsistent"),
    Rule("NR09_change_unsure", "The person was not sure the vendor asked for the change.",
         lambda f, m: "unsure" in f.change_requested),
    Rule("NR10_pressure", "The person pressed for approval.", lambda f, m: "pressure_to_approve" in f.flags),
    Rule("NR11_opt_out", "The person asked not to be called again.", lambda f, m: "opt_out" in f.flags),
    Rule("NR12_asked_what_is_checked", "The person asked what is being checked.",
         lambda f, m: "asked_what_is_checked" in f.flags),
    Rule("NR13_another_number", "The person asked us to call a different number.",
         lambda f, m: f.offered_number is True or "asked_to_call_another_number" in f.flags),
    Rule("NR14_answer_not_in_transcript", "An answer is not supported by the transcript turn it cites, or came out of order.",
         lambda f, m: (f.checks is not None and f.checks.answers_grounded is False) or f.answers_in_order is False),
    Rule("NR15_script_violation", "The call broke the script, or the speech guard had to stop the agent.",
         lambda f, m: f.checks is not None and not _clean_script(f.checks)),
    Rule("NR16_other_flag", "The call was flagged for a person to look at.", lambda f, m: "other" in f.flags),
    Rule("NR17_split_speakers", "Two different names were given for the person speaking for the vendor.",
         lambda f, m: f.same_person_yes_and_digits is False or "speaker_changed" in f.flags),
    Rule("NR18_contradictory_answers", "The person gave conflicting answers about the change or their role.",
         lambda f, m: _conflicting(f.change_requested) or _conflicting(f.speaker_role)),  # not companies (spec 0082, D7b)
    Rule("NR19_earlier_call_concern", "An earlier call about this change raised a concern.",
         lambda f, m: _earlier(f, _earlier_concern)),
    Rule("NR20_manual_answers", "These answers were entered by hand, so a person must review them before release.",
         lambda f, m: m.source == "manual"),
    Rule("NR21_line_risk", "The phone line shows signs of being forwarded or taken over.", lambda f, m: f.line_risk is True),
)

# Block 3: Verified, every condition required ----------------------------------------------------

VERIFIED: tuple[Rule, ...] = (
    Rule("V01_number_eligible", "We dialed the number the Trust Ledger chose for this change.",
         lambda f, m: f.dialed_eligible_number is True,
         "The call did not report dialing the number the Trust Ledger chose."),
    Rule("V02_outbound_dial", "We placed the call, outbound, to the number we chose.",
         lambda f, m: f.dialed_out is True and f.dialed_number_is_pick is True,
         "We could not confirm the call was outbound to the number we chose."),
    # Spec 0080, Part B: with no label (Twilio's "unknown", silence at pickup), digits typed on the keypad
    # show a live person; a machine can't type them. A "machine" label still goes to review (NR01). Both
    # sentences are unchanged, so they stay true for decisions earlier engine versions recorded.
    Rule("V03_live_person", "A person, not a machine, answered and gave the answers.",
         lambda f, m: f.call_result == "answers_recorded" and (
             f.answered_by == "human"
             or (f.answered_by is None and f.digits_typed is True
                 and f.last_four_result not in (None, "not_provided"))),
         "We could not confirm a person, not a machine, answered."),
    Rule("V04_recording_consent", "The person agreed to the recorded call before anything else.",
         lambda f, m: _only(f.recording_consent, "yes") and f.consent_first is True,
         "Recording consent was not given first."),
    # V05_company_matches is retired (spec 0082, D7b): the company answer is no longer required.
    Rule("V06_speaker_identified", "The person gave their name and role.",
         lambda f, m: f.speaker_named is True and any(r.strip() for r in f.speaker_role),
         "The person did not give both their name and role."),
    Rule("V07_right_person_confirmed", "The person confirmed the vendor asked for the change, and is not someone from the request.",
         lambda f, m: _only(f.change_requested, "yes") and f.speaker_in_change_thread is False,
         "We could not confirm that the right person confirmed the change."),
    Rule("V08_digits_match", "The digits the person gave match the new account.", lambda f, m: f.last_four_result == "match",
         "The person did not give matching digits for the new account."),
    Rule("V09_same_person", "One person gave both the confirmation and the digits.",
         lambda f, m: f.same_person_yes_and_digits is True,
         "We could not confirm that one person gave both the confirmation and the digits."),
    Rule("V10_answers_grounded", "Every answer appears in the transcript turn it cites, in order.",
         lambda f, m: f.checks is not None and f.checks.answers_grounded is True and f.answers_in_order is True,
         "We could not confirm every answer is in the transcript, in order."),
    Rule("V11_script_clean", "The disclosure came first and the script was followed.", lambda f, m: _clean_script(f.checks),
         "We could not confirm the script was followed."),
    Rule("V12_no_flags", "Nothing on the call was flagged.", lambda f, m: f.flags == () and f.offered_number is False,
         "Something on the call was flagged."),
)

STRICT: tuple[Rule, ...] = (
    Rule("S01_known_contact", "The person is a contact known from before the change request.",
         lambda f, m: f.speaker_known_before_request is True and f.line_risk is not True,
         "The person is not a contact known from before the change request."),
)

FIXED = {
    INVALID: "Something recorded about this call is not in the expected form, so a person must review it.",
    ENGINE_ERROR: "The decision engine hit an error, so a person must review this change.",
    NOT_MET: "Not every condition for verifying this change was met, so a person must review it.",
}

# Which rules read each recorded answer (spec 0035), so the dashboard can link an answer to the
# rules it fed without knowing the rules. Display only: decide() never reads this table.
# Order and identity facts read several slots' turns (facts.answers_in_order, facts.same_person).
SLOT_RULES: dict[str, tuple[str, ...]] = {
    "recording_consent": ("V04_recording_consent", "V10_answers_grounded", "NR02_no_recording_consent",
                          "NR14_answer_not_in_transcript"),
    "company_named": ("NR03_wrong_company",),
    "department": (),
    "speaker_name": ("V06_speaker_identified", "V07_right_person_confirmed", "V09_same_person", "V10_answers_grounded",
                     "NR04_speaker_from_change_thread", "NR14_answer_not_in_transcript", "NR17_split_speakers",
                     "S01_known_contact"),
    "speaker_role": ("V06_speaker_identified", "NR18_contradictory_answers"),
    "change_requested": ("V07_right_person_confirmed", "V09_same_person", "V10_answers_grounded", "NV01_vendor_denied_change",
                         "NR09_change_unsure", "NR14_answer_not_in_transcript", "NR17_split_speakers",
                         "NR18_contradictory_answers"),
    # V03 reads the digits' capture method since spec 0080, Part B (an unlabeled call verifies with typed digits).
    "account_last4": ("V03_live_person", "V08_digits_match", "V10_answers_grounded", "NV02_digits_mismatch",
                      "NV03_digits_match_old_account",
                      "NR05_digits_near_miss", "NR06_digits_not_provided", "NR07_digits_ambiguous", "NR08_digits_inconsistent",
                      "NR14_answer_not_in_transcript"),
    "offered_number": ("V12_no_flags", "NV04_offered_number_from_request", "NR13_another_number"),
    "flag": ("V12_no_flags", "NR10_pressure", "NR11_opt_out", "NR12_asked_what_is_checked", "NR13_another_number",
             "NR16_other_flag", "NR17_split_speakers"),
}

# An earlier attempt's answers count only against the change (spec 0005, D2): a consented "no"
# or wrong digits decide Not verified, and anything else it raised is NR19.
EARLIER_SLOT_RULES: dict[str, tuple[str, ...]] = {
    "recording_consent": ("NR19_earlier_call_concern",),
    "change_requested": ("NV01_vendor_denied_change", "NR19_earlier_call_concern"),
    "account_last4": ("NV02_digits_mismatch", "NV03_digits_match_old_account", "NR19_earlier_call_concern"),
    "flag": ("NR19_earlier_call_concern",),
}
