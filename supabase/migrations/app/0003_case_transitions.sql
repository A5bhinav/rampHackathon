-- app: the transition table. Owner: Abhi.
--
-- These rows are the state machine from the design doc ("Case state machine · 10 states,
-- 2 moves that need a person"), plus the moves the doc describes in prose: a second change
-- re-holds an unfinished case, the three-business-day deadline, a command that keeps
-- failing, and the approver's rejection. CI redraws docs/state-machine.md from this table.
--
-- Columns: from_state (null = new case), event_type, actor_kind, guard_key, to_state.
-- Guards are evaluated by app.eval_guard(); each (from, event) pair's guards are exclusive.

insert into app.case_transitions (from_state, event_type, actor_kind, guard_key, to_state, description) values
  -- Detect and hold
  (null,               'change_detected',        'system', 'no_unfinished_case',        'open',             'A bank change for a vendor with no unfinished case opens a case'),
  ('open',             'hold_confirmed',         'system', 'readback_ok',               'held',             'Read-back shows the hold'),
  ('open',             'hold_failed',            'system', 'always',                    'needs_review',     'Retries used up; the hold was never confirmed'),
  -- Choose a number
  ('held',             'number_resolved',        'system', 'number_selected',           'calling',          'The Trust Ledger rule passed'),
  ('held',             'no_provable_number',     'system', 'always',                    'needs_review',     'No number with two independent sources that predate the request'),
  -- Call
  ('calling',          'call_ended',             'system', 'answers_recorded',          'deciding',         'Answers recorded'),
  ('calling',          'call_ended',             'system', 'no_person_tries_left',      'retry_wait',       'No person reached; tries left'),
  ('calling',          'call_ended',             'system', 'no_person_tries_exhausted', 'needs_review',     'No person reached; no tries left'),
  ('calling',          'call_timeout',           'system', 'always',                    'needs_review',     'The call never reported back'),
  ('retry_wait',       'retry_timer_fired',      'system', 'in_calling_window',         'calling',          'Inside the calling window'),
  -- Decide
  ('deciding',         'decision_made',          'system', 'outcome_verified',          'awaiting_release', 'Verified'),
  ('deciding',         'decision_made',          'system', 'outcome_not_verified',      'blocked',          'Not verified: the hold stays and the controller is alerted'),
  ('deciding',         'decision_made',          'system', 'outcome_needs_review',      'needs_review',     'Needs review'),
  -- People
  ('needs_review',     'review_cleared',         'person', 'reason_given_not_changer',  'awaiting_release', 'Not the changer; written reason'),
  ('needs_review',     'review_blocked',         'person', 'reason_given',              'blocked',          'Written reason'),
  ('awaiting_release', 'release_co_approved',    'person', 'first_approver',            'awaiting_release', 'Unknown changer: first of two approvers (ADR-10)'),
  ('awaiting_release', 'release_approved',       'person', 'four_eyes',                 'releasing',        'Approver role; not the changer nor the reviewer; version matches; second approver when the changer is unknown'),
  ('awaiting_release', 'release_rejected',       'person', 'four_eyes_reject',          'blocked',          'Approver rejects with a written reason'),
  -- Release
  ('releasing',        'hold_release_confirmed', 'system', 'readback_ok',               'released',         'Read-back shows no hold'),
  -- Another change while the case is unfinished: back to held, version + 1, earlier decision void
  ('open',             'change_detected',        'system', 'always',                    'open',             'Another change before the hold is confirmed: same case, version + 1'),
  ('held',             'change_detected',        'system', 'always',                    'held',             'Another change: version + 1'),
  ('calling',          'change_detected',        'system', 'always',                    'held',             'Another change: back to held, version + 1'),
  ('retry_wait',       'change_detected',        'system', 'always',                    'held',             'Another change: back to held, version + 1'),
  ('deciding',         'change_detected',        'system', 'always',                    'held',             'Another change: back to held, version + 1, decision void'),
  ('awaiting_release', 'change_detected',        'system', 'always',                    'held',             'Another change: back to held, version + 1, decision void'),
  ('needs_review',     'change_detected',        'system', 'always',                    'held',             'Another change: back to held, version + 1'),
  ('blocked',          'change_detected',        'system', 'always',                    'held',             'Another change: back to held, version + 1, decision void'),
  ('releasing',        'change_detected',        'system', 'always',                    'held',             'Another change during release: back to held, version + 1'),
  -- Deadline: three business days with the hold still on
  ('held',             'deadline_reached',       'system', 'always',                    'needs_review',     'Deadline passed; hold stays on'),
  ('calling',          'deadline_reached',       'system', 'always',                    'needs_review',     'Deadline passed; hold stays on'),
  ('retry_wait',       'deadline_reached',       'system', 'always',                    'needs_review',     'Deadline passed; hold stays on'),
  ('deciding',         'deadline_reached',       'system', 'always',                    'needs_review',     'Deadline passed; hold stays on'),
  -- A command that keeps failing (R7: fail closed)
  ('held',             'command_failed',         'system', 'always',                    'needs_review',     'A command kept failing; hold stays on'),
  ('calling',          'command_failed',         'system', 'always',                    'needs_review',     'A command kept failing; hold stays on'),
  ('retry_wait',       'command_failed',         'system', 'always',                    'needs_review',     'A command kept failing; hold stays on'),
  ('deciding',         'command_failed',         'system', 'always',                    'needs_review',     'A command kept failing; hold stays on'),
  ('releasing',        'command_failed',         'system', 'always',                    'needs_review',     'Release kept failing; hold stays on');
