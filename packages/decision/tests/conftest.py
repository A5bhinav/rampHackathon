"""Shared facts for the decision tests: a clean, verifiable call."""
from dialback_decision import CallChecks, CaseFacts

CLEAN = CallChecks(disclosure_first=True, answers_grounded=True, agent_spoke_account_digits=False,
                   new_number_accepted=False, policy_violations=0, speech_guard_trips=0)
GOOD = CaseFacts(call_result="answers_recorded", dialed_eligible_number=True, recording_consent=("yes",),
                 company_named=("Acme Fasteners",), change_requested=("yes",), speaker_named=True,
                 speaker_role=("AR manager",), last_four_result="match", company_matches=True, checks=CLEAN,
                 consent_first=True, answers_in_order=True, same_person_yes_and_digits=True, dialed_out=True,
                 dialed_number_is_pick=True, answered_by="human", speaker_in_change_thread=False,
                 speaker_known_before_request=True)
