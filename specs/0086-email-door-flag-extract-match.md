# 0086: Email door, flag, extract and match (PR 3 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

Read an email that reached the accounts-payable inbox and decide whether it asks to change where a vendor's payments go (a bank-change request), pull out what the email says about the new destination without keeping a full account number (ADR-8), and match the email to a vendor. This PR is built in three tasks: Task 5 writes the labeled test emails (this stub covers it), Task 6 writes flagging, extraction and redaction, Task 7 writes vendor matching. Emails are data, never instructions (R9).

## Contract touched

None.

## Acceptance

- [ ] Task 5: 92 tuning and 100 held-out labeled emails exist, one JSON file each (`subject`, `sender`, `text`, `label`, `vendor`, `note`), and the held-out half is sealed by a SHA-256.
- [ ] Task 6, flagging: `flag(subject, text)` flags an email when, in one sentence of the subject or body, a change word sits near a word for where money goes (a change verb within 8 words, a change adjective just before it, or a correction or a closed account after it), or when a core change verb (change, update, switch, move, replace and the like) and a destination word both appear anywhere in the email (revision 1). A change verb that is negated or about requests in general does not count, nor a core change verb that is conditional or about a process, nor anything in a sentence about fraudsters or phishing, nor a destination word after "your" or before "remains the same". Quoted and forwarded text is read like any other text. The bar, set before tuning: in each half, every `change_request` is flagged and at most 3 `ordinary` emails are (an absolute count). **The rule alone did not meet it on the held-out half** (see the results below). Under Sami's decision 11 an email is flagged when the rule OR a quarantined model says so, and the bar applies to that combined flagger on a fresh held-out set (a later task). The rule keeps the bar on the tuning half and a floor on the old held-out half, now development data: at least 30 of 35 change requests and at most 3 of 65 ordinary emails. Reads the first 50,000 characters of subject plus text, in time near-linear in their length.
- [ ] Task 6, the 50,000-character cap (`MAX_CHARS`): `flag`, `find_routing`, `find_accounts`, `redact` and `redact_for_model` each read only the first 50,000 characters of the text (`flag`: subject first, then text), so none can be slowed without bound. The cap is also a suppression route: a sender can pad an email and put the request after the padding, and a long quoted thread can push a real request past it. Text past the cap is never returned by `redact` or `redact_for_model`.
- [ ] Task 6, extraction: `find_routing` returns each run of exactly nine digits with a valid ABA checksum (`aba_valid`, moved to `dialback_core/bank.py`). `find_accounts` returns each run of 4 to 17 digits that starts within 40 characters after an account label (`Account`, `Acct`, `A/C`, with only label words such as "number", "#", "is" between), grouped by `masking.py`'s separators, as digits only. A last-four reference ("the account ending 8888") is not an account. `find_routing` never returns a run taken for an account (below). `find_accounts` is the only function that returns a full account number; nothing logs. `aba_valid` refuses a trailing newline, and so does the clerk route's account check.
- [ ] Task 6, redaction (ADR-8), the barrier for both the ledger and the model: `redact(text, also_mask=())` masks every labeled account number in its grouped form to its last four, and every other run of 5 or more digits, except phone numbers (found line by line with the ledger's own matcher, so its stub reader still sees them) and nine-digit routing numbers with a valid checksum. It also treats as an account, so masks: any digits repeating a found account (or one passed in `also_mask`, such as the body's accounts when redacting the subject), however grouped; a run whose nearest label on its line is an account label; a phone-shaped number on a line with a payment word (account, acct, routing, ABA, wire, ACH, remit, pay, bank, IBAN, deposit, beneficiary) and no phone word (call, phone, tel, mobile, cell, fax, contact, reach, text, ext); and any phone extension, which is also never a routing number. A line of digits and punctuation only takes the payment and phone words of the last line before it that has letters (line breaks as `str.splitlines` counts them). Payment words do not include "accounts payable", "accounts receivable", "account manager" or "payable", and "ph" is a phone word, so signature lines keep their phones. When a phone's digits cannot be lined up with its number and there are extra digits, the whole match is masked. `redact_for_model(text)` NFKC-normalizes the text and replaces every digit with `#`: the model's copy has no digits at all.
- [ ] Task 6, fingerprint: `requested_fingerprint(key, routing, account)` is `HMAC-SHA256(key, routing|account)` (ADR-8).
- [ ] Vendor matching: to be completed by Task 7.

## Tests (written first)

Fixtures live in `apps/core/tests/email_door/fixtures/`.

- `tuning/`: 30 `change_request` and 62 `ordinary` emails (`cr-NN.json`, `ord-NN.json`). Three of the change requests are the `vendors.*.request` emails of `packages/ledger/src/dialback_ledger/demo_seed.json`, span text joined by newlines. The flagging code may be tuned against these.
- `heldout/`: 35 `change_request` and 65 `ordinary` emails, written to the same brief with no sentence shared with the tuning half. Five of the change requests (cr-31..35) use scenarios absent from the tuning half, and several ordinary emails carry full routing and account digits. Not to be read or run against until the flagging code is final.
- `heldout.sha256` holds the output of `cat $(ls heldout/*.json | sort) | shasum -a 256`, so any later edit to the held-out half is visible:

```
6235aebe8431bfa4a907b6022e85af011c596480bc2885ec646f86ef39242edb  -
```

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/email_door/test_flag_bar.py` (both tests check the label counts; the development-half test first recomputes the hash) |
| 2 | `test_rules.py::test_flag_pairs_a_change_word_with_a_payment_destination_in_a_sentence_or_a_core_verb_in_the_email`, `::test_flag_skips_negated_hypothetical_and_how_to_sentences`, `::test_an_invoice_with_remit_to_details_is_not_flagged`, `::test_forwarded_request_is_flagged`, `::test_a_correction_to_the_details_is_a_change`, `::test_flag_examples`, `::test_quoted_paragraphs_split_like_plain_ones`, `::test_flag_is_fast_on_a_long_unpunctuated_email`, `::test_flag_reads_only_the_first_50000_characters`, `::test_every_text_function_reads_only_the_first_50000_characters`; `test_flag_bar.py::test_tuning_half_meets_the_bar`, `::test_rule_alone_keeps_its_floor_on_the_development_half` |
| 3 | `test_rules.py::test_aba_valid_refuses_a_trailing_newline`, `::test_routing_needs_the_aba_checksum`, `::test_accounts_are_found_grouped_or_labeled`, `::test_accounts_need_a_label_and_four_to_seventeen_digits`, `::test_a_last_four_reference_is_not_an_account`, `::test_a_loosely_labeled_account_is_not_a_routing_number`; `apps/core/tests/test_clerk_change.py::test_a_malformed_number_is_refused_without_echoing_it` (uses `aba_valid`; trailing-newline cases) |
| 4 | `test_rules.py::test_redact_leaves_only_the_last_four`, `::test_redact_keeps_phone_and_routing`, `::test_redact_masks_an_unlabeled_number_on_a_phone_line`, `::test_redact_keeps_a_valid_routing_number_and_masks_an_invalid_one`, `::test_a_labeled_account_is_masked_even_when_it_looks_like_a_phone_or_a_routing_number`, `::test_redact_masks_at_least_what_masking_masks`, `::test_a_found_account_is_masked_wherever_it_repeats`, `::test_redact_masks_accounts_found_in_another_field`, `::test_a_phone_shaped_number_on_a_payment_line_is_masked`, `::test_a_phone_on_a_line_with_a_phone_word_or_no_payment_word_stays`, `::test_a_phone_extension_of_five_digits_or_more_is_masked`, `::test_an_extension_is_masked_in_any_form`, `::test_an_extension_is_never_a_routing_number`, `::test_a_number_alone_on_a_line_takes_the_words_of_the_line_before`, `::test_a_phone_alone_on_a_line_after_a_phone_word_stays`, `::test_signature_lines_keep_their_phones`, `::test_redact_is_fast_on_a_long_email`, `::test_the_model_sees_no_run_of_five_digits`, `::test_the_model_copy_has_no_digit_at_all` |
| 5 | `test_rules.py::test_fingerprint_matches_adr8_material` |
| 6 | to be completed by Task 7 |

## Out of scope

Opening cases from flagged emails, replying to the sender, and any change to the receiving route (spec 0085).

## Open questions

- Whether a new-vendor onboarding email that carries first-time bank details counts as a change request. The fixtures avoid this case.

## Commands

```bash
cat $(ls apps/core/tests/email_door/fixtures/heldout/*.json | sort) | shasum -a 256
```

## Task 6 flagging results (every rule version)

The bar is fixed: in each half, every `change_request` flagged and at most 3 `ordinary` flagged. Versions 1 to 5 were designed on the tuning half plus probe sentences the builder wrote (none taken from either half); the held-out half was not opened. Version 5 was declared final and run on the held-out half once.

| Version | What changed | Tuning: change requests flagged | Tuning: ordinary flagged | Held-out: change requests flagged | Held-out: ordinary flagged |
|---|---|---|---|---|---|
| 1 | change word near a destination word in one sentence; negated, conditional, how-to and fraud-awareness sentences skipped | 30/30 | 0/62 | not run | not run |
| 2 | closed or frozen accounts, "from today"; the reader's own ("your") payment method, customer accounts, online banking, remittance advice and bare "remittance" (verbs only) no longer count | 30/30 | 0/62 | not run | not run |
| 3 | "modified"; "please process" is not a process noun; "change requests" in general and "remains the same" do not count | 30/30 | 0/62 | not run | not run |
| 4 | "bankers", "remittances", "previous" and "former", an address for checks | 30/30 | 0/62 | not run | not run |
| 5 (final) | "routing" only before a number word, digits or a colon; "SWIFT" only as SWIFT code; "New York" and "Old National" are names | 30/30 | 0/62 | **25/35** (missed cr-02, cr-08, cr-11, cr-13, cr-14, cr-24, cr-29, cr-32, cr-34, cr-35) | 1/65 (ord-29) |

Version 5 fails the held-out bar on recall. Per the stop rule, at most two revisions follow, designed on the tuning half only, each re-run on the held-out half. Any result after the first held-out run is exploratory: the held-out file names of the misses are now known, though their text is not.

| Version | What changed | Tuning: change requests flagged | Tuning: ordinary flagged | Held-out: change requests flagged | Held-out: ordinary flagged |
|---|---|---|---|---|---|
| 6 (revision 1) | a core change verb (change, update, switch, replace, move, redirect, modify, migrate, transition, amend, alter, revise) anywhere in the email pairs with a destination word anywhere in it (the noun "any update" excluded); "payee", "particulars"; a change adjective on "details", "instructions" or "information"; "different" after a destination word; "future", "from now on" and the like near "payments", "remittances" or "funds" | 30/30 | 0/62 | **29/35** (missed cr-08, cr-11, cr-14, cr-24, cr-32, cr-35) | 2/65 (ord-29, ord-54) |
| 7 (revision 2, in the code) | change words "merged", "acquired", "opened", "transferred", "relocated", "rerouted", and "not ... anymore" or "any longer"; "credit union" and "financial institution" as destinations; "error", "mistake", "misprint", "inaccurate" after a destination word; the conditional, process and "for" filters apply to core change verbs only | 30/30 | 0/62 | **30/35** (missed cr-08, cr-14, cr-24, cr-32, cr-35) | 2/65 (ord-29, ord-54) |

**Stopped.** Version 7 still fails the held-out bar on recall after the two revisions the stop rule allows, so no further revision was tried. Versions 6 and 7 were designed after seeing version 5's held-out misses by file name (never their text), so their held-out numbers are exploratory, not a clean held-out result. Precision held throughout (at most 2 of 65 ordinary emails flagged); recall did not. Decision for Sami: how to proceed (for example a fresh, unseen held-out set for a new approach, or a ruling on the bar).

### After the stop

- **The rule alone did not meet the bar.** The only clean held-out result is version 5's: 25/35 change requests and 1/65 ordinary. Versions 6 and 7 (29/35 and 2/65; 30/35 and 2/65) are exploratory.
- **Sami's decision 11:** an email is flagged when the rule OR a quarantined model says so (a later task builds the model). The bar now applies to the combined flagger, judged on a fresh held-out set. The old held-out half is development data: `test_rule_alone_keeps_its_floor_on_the_development_half` keeps its hash check and requires the rule alone to flag at least 30 of 35 change requests and at most 3 of 65 ordinary emails. `test_tuning_half_meets_the_bar` is unchanged.
- **"The rule said no" is never evidence.** The rule can be switched off on purpose: one sentence about fraud skips everything in it, so "Due to a recent phishing attempt, we have changed our bank. Routing 026009593, account 000123454821." is not flagged, while the same email without that opening clause is.
- **Change made after the stop (review fix round 1, found by code reading, not by any result):** the time phrase "moving forward" had counted as the core change verb "move", because the core-verb check matched only a prefix. It now matches whole terms. Afterwards, on both halves: tuning 30/30 and 0/62; development 30/35 (missed cr-08, cr-14, cr-24, cr-32, cr-35) and 2/65 (ord-29, ord-54), the same as version 7. The same round made flagging near-linear and capped at 50,000 characters (outputs on the tuning half and the builder's probe sentences byte-for-byte unchanged), and tightened extraction and redaction (no flagging decision changed).

### What redaction still lets through (review fix round 2)

The model's copy (`redact_for_model`) has no digits at all. The ledger's copy (`redact`) keeps phone numbers and routing numbers on purpose, so these residuals remain there:

- A phone-shaped account number with no account label on a line that also has a phone word ("Call or wire 7783110245"): kept as a phone.
- An unlabeled nine-digit account number that happens to pass the ABA checksum (about 1 in 10), with no account label before it on its line: kept as a routing number.
- A repeat of a found account written without its leading zeros (account `000123454821`, repeated as `123454821`): not recognized as a repeat, so kept when it is phone-shaped or passes the ABA checksum.
