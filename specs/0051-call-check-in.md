# 0051: Checking in when the line goes quiet, at every question

**Owner:** Sami  **Slice:** call and adversary  **Target day:** Nov 10, 2026. Sami set this on Nov 9,
overriding the freeze dates in AGENTS.md.
**Builds on:** 0043 (keypad-only digits, PR #96; this branch is stacked on it), 0012 (the call
policy's events).

## Goal

On a live call today, "Sorry, are you still there?" effectively never plays.

- The policy plays it on a `Silence` event (`packages/policy/.../step.py`). The relay makes a
  `Silence` only from an empty final transcription (`map_message` in
  `apps/voice/.../twilio/relay.py`).
- When the line is simply quiet, Twilio sends nothing, and `_Inbox.next_event` waits forever.
- So a caller who goes quiet, to find their statement or because they walked away, leaves the call
  open until its overall time limit, with no prompt.
- The text line produces silence on its own timer (`transport.py`), which is why tests never caught
  this.

A second problem becomes live once the check-in fires. The check-in doesn't repeat the question, so
a reply like "Yes, I'm here" is recorded as the answer to it. `docs/live-call.md` already warns
about this. At the change question, that reply would be a "yes, we asked for the change".

When this spec is done:

- **Quiet is noticed at every question.** The relay notices when nothing has come from the caller
  for a while after the agent finishes speaking, and the call checks in.
- **A reply to the check-in only shows the caller is there.** It is never recorded as an answer, and
  the question is asked again.
- **No reply to the check-in ends the call politely.** Dialback calls again later under the core's
  existing rules.

It serves R7 (a quiet line ends closed, never as an answer), R3 (code, not the model, decides what
counts as activity) and R8 (nothing here interrupts the disclosure).

## Decisions

- **D1. Timings** (Sami asked for the author's best values on Nov 9; all are settings):

  | Setting | Value | What it is |
  |---|---|---|
  | `QUIET_SECONDS` | 10 | Quiet after the agent finishes a question, before the check-in |
  | `QUIET_SECONDS_LAST4` | 20 | The same at the digits question, since finding a statement takes time |
  | `AFTER_CHECK_IN_SECONDS` | 10 | Quiet after the check-in itself, before the call ends |

- **D2. Activity is decided by code, never the model.** Any message from the caller's side counts as
  activity and stops the quiet clock:
  - a `prompt`, partial or final (someone mid-sentence is never interrupted);
  - a `dtmf` key;
  - Twilio's `clientSpeaking` event.

  The model keeps its one job, reading what an answer means (R3).
- **D3. When the clock starts.** The TwiML subscribes to `events="speaker-events"`, so Twilio
  reports `agentSpeaking` and `clientSpeaking`.
  - The clock starts when Twilio reports the agent stopped speaking.
  - That message's exact format isn't in Twilio's WebSocket docs (Q117). So if none arrives, the
    clock starts at an estimate of the line's length: 1 second plus 1 second per 12 characters
    from when the line was sent, plus 3 seconds of margin. The estimate errs late, so the check-in
    can come a little late, never early.
  - While the agent is speaking, no clock runs.
- **D4. The check-in.** When the clock runs out, the relay emits `Silence`, and the policy says:
  - at any question but the digits one: "Sorry, are you still there?";
  - at the digits question: "Take your time. When you have the last four digits, enter them on your
    keypad, then press the pound key."
- **D5. A reply to the check-in is presence, not an answer.** After a check-in:
  - **The next spoken turn is never recorded and never sent to the model,** whatever it says. The
    call says "Thanks." and asks the current question again, in its fixed wording.
  - **This re-ask doesn't use up spec 0043's one more try,** because presence isn't unclear.
  - **At the digits question,** a keypad entry after the check-in is the answer (keys are
    unambiguous) and goes through spec 0043's rules as usual. A spoken turn there is presence, as
    above.
  - **A keypad entry or star key at any other question** after a check-in also counts as presence.
- **D6. No reply ends the call.** If `AFTER_CHECK_IN_SECONDS` pass with no activity after a check-in:
  - the call ends `failed` with reason `silence` and today's goodbye line;
  - nothing new is recorded;
  - the core then redials, or sends the case to a person, by its existing rules for a `failed` call
    (spec 0043, D5).

  `MAX_SILENCES` becomes 2 *consecutive* silences: the check-in, then the end. Any activity resets
  the count. A whole-call cap of 3 check-ins (`MAX_CHECK_INS`) stops a caller who keeps going quiet;
  the fourth quiet spell ends the call the same way.
- **D7. The keypad timer is unchanged.** Spec 0043's 10-second wait after the last key still decides
  when a typed entry goes in. The quiet clock runs only while nothing has been typed.
- **D8. The text line.** `transport.py`'s simulated silence keeps working. Its `Silence` events flow
  through the same policy rules (D4 to D6), so text-line and live behavior match.

## Contract touched

None. `Silence` is an internal policy event, `failed`/`silence` is an existing `CallEndedBody`
result and reason, and `events="speaker-events"` is TwiML.

## Acceptance

- [ ] **C1. The quiet clock (D1 to D3).** Through `run_relay`, with short test timings:
  - after an agent line and no caller message, a `Silence` reaches the policy once the clock runs out;
  - a partial `prompt`, a final `prompt`, a `dtmf` key or a `clientSpeaking` message before then
    stops it;
  - with no agent-stopped-speaking message, the clock starts at the estimate;
  - with one, it starts there;
  - the digits question waits `QUIET_SECONDS_LAST4`.
- [ ] **C2. The check-in (D4).** The first `Silence` plays "Sorry, are you still there?", or the
  digits line at the digits question. Both lines pass the speech guard (R4).
- [ ] **C3. Presence, not an answer (D5).**
  - At the change question, "Yes, I'm here" after a check-in records nothing, never reaches the
    model, and is followed by "Thanks." and the change question again.
  - A following "Yes." is then recorded as the answer.
  - At the consent question, the same: a presence turn never becomes consent.
  - At the digits question, typed `4 8 2 1 #` after the check-in is recorded as the last four.
  - None of these uses up the one more try.
- [ ] **C4. No reply ends the call (D6).**
  - A second consecutive `Silence` ends the call `failed`/`silence` with the goodbye line, and
    records nothing new. The report has no `answers` for a call that ended before any answer.
  - Activity between two quiet spells resets the count.
  - A fourth quiet spell in one call ends it even with activity in between.
- [ ] **C5. TwiML.** It carries `events="speaker-events"`, alongside `dtmfDetection="true"`,
  `interruptible="false"` and `reportInputDuringAgentSpeech="dtmf"`.
- [ ] **C6. Fail closed.** An unknown or malformed `info` message is ignored and never stops or starts
  the clock wrongly. A socket error during the quiet wait is a `Hangup`, as today.
- [ ] **C7. Docs.** In `docs/live-call.md`, the "are you still there?" row says to say anything: the
  question will be asked again, and the reply is never taken as an answer.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| C1, C6 | `apps/voice/tests/test_relay.py::test_quiet_clock_*` (one per bullet), `::test_unknown_info_message_is_ignored` |
| C2 | `packages/policy/tests/test_step.py::test_silence_checks_in`, `::test_digits_check_in_line`; the guard check over every fixed line in `test_script.py` |
| C3 | `packages/policy/tests/test_step.py::test_reply_to_check_in_is_presence_not_an_answer` (consent, change, free text), `::test_keys_after_digits_check_in_are_the_answer` |
| C4 | `packages/policy/tests/test_step.py::test_no_reply_after_check_in_ends_failed_silence`, `::test_activity_resets_the_quiet_count`, `::test_check_in_cap` |
| C5 | `apps/voice/tests/test_twiml.py::test_speaker_events_are_subscribed` |
| C7 | review of the doc |

## Out of scope

- Letting the caller interrupt the agent (`interruptible` stays `false`, R8).
- Changing the core's redial timing for a `failed` call.
- A different voice or wording for the disclosure.

## Open questions

- **Q117. Twilio's speaker events.** What exact message marks the agent stopping, for example
  `{"type": "info", "name": "agentSpeaking", "value": "off"}`? Confirm on the first live call; until
  then D3's estimate carries the clock.
- **Q118. The estimate.** Is 1 second plus 1 second per 12 characters, plus 3 seconds, right for the
  TTS voice in use? It only matters when Q117's message doesn't arrive.

## Commands

```bash
cd packages/policy && uv run pytest -q
cd apps/voice && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages
```
