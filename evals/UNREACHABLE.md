# Decision rules the rule-removal sweep cannot see (spec 0048, A5)

`lab sweep` removes each decision rule in turn and runs every voice scenario and 2000 generated call
reports per domain (seed 0) against the smaller table. A removal is **seen** when some outcome changes.
Every rule must be seen except the ones below, each with its reason. Reviewed by Abhi, who owns the rules
(Q103). The sweep fails when a rule is neither seen nor listed, and when a listed rule turns out to be seen
(take it off the list).

A rule here is still tested: `packages/decision`'s unit tests cover every rule, and a voice scenario's
`expect_rule` fails `lab run` when the rule it names stops firing (NR05, NR07 and NR10 have one each). What
the sweep cannot show is an **outcome** that depends on the rule, because another rule already gives the
same outcome whenever it fires.

| Rule | Reason |
|---|---|
| `NR03_wrong_company` | Another rule gives the same outcome: it fires when `company_matches` is False, and V05 then fails too, so without it the call still needs review (NR99). |
| `NR04_speaker_from_change_thread` | Another rule gives the same outcome: it fires when the speaker is in the change thread, and V07 (which needs them not to be) then fails too. |
| `NR05_digits_near_miss` | Another rule gives the same outcome: a near miss is not a match, so V08 fails too. l18 names it in `expect_rule`. |
| `NR06_digits_not_provided` | Another rule gives the same outcome: no digits is not a match, so V08 fails too. |
| `NR07_digits_ambiguous` | Another rule gives the same outcome: when the old and new accounts end alike the result is never a match, so V08 fails too. l20 names it in `expect_rule`. |
| `NR08_digits_inconsistent` | Another rule gives the same outcome: two sets of digits are never a match, so V08 fails too. |
| `NR09_change_unsure` | Another rule gives the same outcome: an "unsure" answer means the change is not only "yes", so V07 fails too. |
| `NR10_pressure` | Another rule gives the same outcome: the pressure flag is a flag, so V12 fails too. a16 names it in `expect_rule`. (Pressure said in the consent answer itself is flagged at that turn, so consent is not first and NR02 decides instead: a finding, reported with spec 0048.) |
| `NR11_opt_out` | Another rule gives the same outcome: the opt-out flag is a flag, so V12 fails too. |
| `NR12_asked_what_is_checked` | Another rule gives the same outcome: the flag is a flag, so V12 fails too. |
| `NR13_another_number` | Another rule gives the same outcome: an offered number or the "call another number" flag fails V12 too. |
| `NR14_answer_not_in_transcript` | Another rule gives the same outcome: an ungrounded answer, or answers out of order, fails V10 too. |
| `NR15_script_violation` | Another rule gives the same outcome: checks that are not clean fail V11 too. |
| `NR16_other_flag` | Another rule gives the same outcome: the flag is a flag, so V12 fails too. |
| `NR17_split_speakers` | Another rule gives the same outcome: two names fail V09 (one person), and the "speaker changed" flag fails V12. |
| `NR20_manual_answers` | No call or generated report reaches it: it fires only on answers entered by hand (`source="manual"`), which the call path never uses. |
| `V04_recording_consent` | Another rule gives the same outcome: whenever consent is refused, contradicted, missing or not first while other answers exist, NR02 fires first (block 0); with no answers at all, V06 and V07 fail too. |
| `V08_digits_match` | Another rule gives the same outcome: every last-four result but a match fires a digits rule (NV02, NV03, NR05 to NR08), and a report with no answers fails NR01. |
| `V09_same_person` | No honest call or generated report reaches it alone: two names fire NR17, and an unknown speaker arises from a name given after the change (NR14, out of order) or a name that cannot be read, which neither the scripted line nor the report generator produces. |
| `V10_answers_grounded` | Another rule gives the same outcome: an ungrounded answer or answers out of order fire NR14; with no change answer V07 fails too, and with no checks V11 fails too. |
| `V11_script_clean` | Another rule gives the same outcome: checks that are not clean fire NR15; with no checks at all V10 fails too. |
| `V12_no_flags` | Another rule gives the same outcome: every flag fires its own needs-review rule (NR10 to NR13, NR16, NR17), and an offered number fires NR13. |

## Seen only through a stand-in

These are seen, so they are not in the table, but only through something production does not do today: a
contract-only report, or a scenario resting on a declared fact (A3). `lab sweep` fails when a rule joins
this list or leaves it (spec 0048 review fix 6), so a rule that stops being seen by anything production does
is noticed.

- `NV04_offered_number_from_request`: only through contract-only reports. No production agent records an
  offered number, so the ledger is never asked about one (spec 0048 review fix 4).
- `NR18_contradictory_answers`: only through contract-only reports. Production records each answer once, so
  no answer contradicts another.
- `NR19_earlier_call_concern`: only through contract-only reports, whose earlier calls carry answers.
  Production never sends answers on an earlier call (FINDINGS A16), so in production it cannot fire.
- `NR21_line_risk`: only through a07, which declares `line_risk` (A3). Nothing in production computes line
  risk yet (FINDINGS A7); spec 0044 adds it.
- `V06_speaker_identified`: only through contract-only reports. Production asks the name and the role in turn
  and records each as said, so a report that reaches the change question holds both; one is missing only on a
  call that ended there, where the change and digits are missing too.
