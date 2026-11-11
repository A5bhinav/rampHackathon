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
- When the line is simply quiet, Twilio sends nothing, and `_Inbox.next_event` waits with no
  timeout.
- `run_relay` has no time limit of its own, and the TwiML sets none. So a caller who goes quiet, to
  find a statement or because they walked away, leaves the call open with no prompt. The only
  backstop is the core's `call_timeout`, which sends the case to a person (`app/0003`).
- The text line simulates silence with a `silent` flag, where every turn is `Silence`
  (`transport.py`). That is why tests never caught this.

A second problem becomes live once the check-in fires. "Sorry, are you still there?" doesn't repeat
the question, so a reply like "Yes, I'm here" is recorded as the answer to it. `docs/live-call.md`
warns about this. At the change question, that reply would be a "yes, we asked for the change".

When this spec is done:

- **Quiet is noticed at every question,** and the call checks in.
- **A reply to the check-in only shows the caller is there.** It is never recorded as an answer, and
  the question is asked again.
- **No reply to the check-in ends the call closed.** At the digits question it keeps every other
  answer and sends the case to a person, as spec 0043 does.

It serves R7 (a quiet line ends closed, never as an answer), R3 (code, not the model, decides what
counts as activity), R9 (a presence reply can never be cited as an answer) and R8 (nothing here
interrupts the disclosure).

## Decisions

- **D1. Timings.** Sami asked for the author's best values on Nov 9. They are module constants in
  the relay, like `KEYPAD_TIMEOUT`, and `run_relay` parameters so tests can shorten them.

  | Setting | Seconds | What it is |
  |---|---|---|
  | `QUIET_SECONDS` | 10 | Quiet after the agent finishes, before the check-in |
  | `QUIET_SECONDS_LAST4` | 20 | The same at the digits question, since finding a statement takes time |
  | `AFTER_CHECK_IN_SECONDS` | 10 | Quiet after a check-in, before the call ends |
  | `AFTER_CHECK_IN_SECONDS_LAST4` | 20 | The same after the digits check-in |

- **D2. Activity is decided by code, never the model.** Any of these from the caller's side is
  activity:
  - a `prompt`, partial or final, including an empty final one (line noise is not quiet);
  - a `dtmf` key;
  - Twilio's `clientSpeaking` event.

  Activity **postpones** the clock: the quiet is measured from the latest activity. Nothing ever
  stops the clock for good. So a cough with no transcript, or a stray key at another question,
  can't leave the call waiting forever. `map_message` no longer turns an empty final prompt into
  `Silence`.

  The model keeps its one job, reading what an answer means (R3).
- **D3. When the clock starts (it is re-armed every time the relay waits for the caller).**
  - The TwiML subscribes to `events="speaker-events"`. Per Twilio's TwiML reference, `events` takes
    `speaker-events`, `tokens-played` and `config-updated`, and `speaker-events` sends
    `agentSpeaking` and `clientSpeaking`.
  - After a step that spoke, the clock starts when Twilio reports the agent stopped speaking. Only
    an "off" that follows an "on" seen since the last line was sent counts, because events can pile
    up while the model answers.
  - If no such message arrives, the clock starts at an estimate. It runs from the moment the last
    line was sent: 1 second plus 1 second per 12 characters of **every** line in the step's `say`,
    including the disclosure at `Connected` (the welcome greeting), plus 3 seconds of margin. The
    estimate's constants are injectable for tests. It errs late, so a check-in can come a little
    late, never early.
  - After a step that said nothing (for example, keys at another question), the clock starts at
    once.
  - The exact speaker-event message is Q117.
- **D4. The check-in.** When the clock runs out, the relay emits `Silence`, and the policy says:
  - at any question but the digits one: "Sorry, are you still there?";
  - at the digits question: "Take your time. When you have the last four digits, enter them on your
    keypad, then press the pound key."

  The check-in turn is an agent turn with `asked = None`.
- **D5. A reply to the check-in is presence, not an answer.** The policy holds
  `check_in_pending: bool` in `State`; it is pure, and the relay holds every timing.
  - **The next spoken turn after a check-in is a presence turn.**
    - It is never recorded and never sent to the model: presence turns are left out of the
      transcript the model gets.
    - `validate` refuses any tool call that cites a presence turn.
    - The call then says "Thanks." and the current question's fixed wording, in one agent turn with
      `asked = phase`.
    - Code's own pressure flag still reads presence turns. It can only add a review.
  - **The re-ask doesn't use up spec 0043's one more try,** because presence isn't unclear.
  - **At the digits question:** a keypad entry after the check-in is the answer, and goes through
    spec 0043's rules as usual. A star key plays spec 0043's start-over line and clears the pending
    check-in.
  - **At any other question:** a key or a star after a check-in counts as presence.
  - **At the callback-time question,** the re-ask uses that question's line.
- **D6. No reply ends the call closed.** If the after-check-in wait passes with no activity:
  - **at the digits question:** the call finishes `answers_recorded` with no digits, keeping every
    other answer, so `decide` gives NR06 and a person sees the full call. This is the same reason
    as spec 0043's Q79;
  - **at any other question:** the call ends `failed` with reason `silence` and today's goodbye line.
    A `failed` report carries no answers, and the core redials, or sends the case to a person, by
    its existing rules.

  What changes in the silence count:
  - `MAX_SILENCES` goes from 3 to 2: the check-in, then the end.
  - `Keypad` and `KeypadReset` now reset the count, as `CalleeSaid` already does.
  - A new whole-call cap, `MAX_CHECK_INS = 3`, ends the call on the fourth quiet spell even with
    activity in between, the same way as above.
- **D7. The keypad timer is unchanged.** Spec 0043's 10-second wait after the last kept key decides
  when a typed entry goes in. While digits are held, the keypad deadline governs, not the quiet
  clock.
- **D8. The text line.** `transport.py`'s `silent` vendor sends `Silence` on every turn. Those flow
  through the same policy rules (D4 to D6), so the text line and live calls behave the same.

## Contract touched

None:
- `Silence` is an internal policy event;
- `failed`/`silence` and `answers_recorded` are existing results;
- `events="speaker-events"` is TwiML.

## Acceptance

- [ ] **C1. The quiet clock (D1 to D3).** Through `run_relay`, with short test timings:
  - after an agent line and no caller message, a `Silence` reaches the policy once the clock runs out;
  - a partial prompt, a final prompt (even empty), a key or a `clientSpeaking` postpones it, and it
    fires once the quiet has lasted from the latest activity;
  - a lone `clientSpeaking` with nothing after it, and a key at another question, still end in a
    `Silence`;
  - with no agent-stopped message, the clock starts at the estimate, which counts the disclosure at
    `Connected`;
  - with one, it starts there, and only an "off" after an "on" since the last send counts;
  - the digits question uses the LAST4 timings.
- [ ] **C2. The check-in (D4).** The first `Silence` plays "Sorry, are you still there?", or the
  digits line at the digits question, as an agent turn with `asked = None`. Both lines pass the
  speech guard (R4).
- [ ] **C3. Presence, not an answer (D5).**
  - At the change question, "Yes, I'm here" after a check-in records nothing, is absent from the
    model's transcript, and is followed by "Thanks." and the change question again. A following
    "Yes." is recorded.
  - A tool call citing the presence turn is refused.
  - At consent, the same: a presence turn never becomes consent.
  - At the digits question, typed `4 8 2 1 #` after the check-in is recorded, and a star plays the
    start-over line.
  - None of these uses up the one more try.
- [ ] **C4. No reply ends the call closed (D6).**
  - At consent or the change question, a second consecutive `Silence` ends `failed`/`silence` with
    the goodbye line.
  - At the digits question, it finishes `answers_recorded` with no digits, and `decide` gives NR06.
  - Activity between two quiet spells resets the count.
  - A fourth quiet spell in one call ends it even with activity in between.
- [ ] **C5. TwiML.** It carries `events="speaker-events"`, alongside `dtmfDetection="true"`,
  `interruptible="false"` and `reportInputDuringAgentSpeech="dtmf"`.
- [ ] **C6. Fail closed.**
  - An unknown or malformed `info` message is ignored. It neither starts nor stops the clock wrongly.
  - A socket error during the quiet wait is a `Hangup`, as today.
- [ ] **C7. The text line (D8).** A `silent` vendor reaches the same check-in, then the same end, as a
  quiet live call.
- [ ] **C8. Docs.** In `docs/live-call.md`, the bullet about "Sorry, are you still there?" says to
  say anything: the question will be asked again, and the reply is never taken as an answer.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| C1, C6 | `apps/voice/tests/test_relay.py::test_quiet_clock_*` (one per bullet), `::test_unknown_info_message_is_ignored` |
| C2 | `packages/policy/tests/test_step.py::test_silence_checks_in`, `::test_digits_check_in_line`; the guard check over every fixed line in `test_script.py` |
| C3 | `packages/policy/tests/test_step.py::test_reply_to_check_in_is_presence_not_an_answer` (consent, change, free text), `::test_a_presence_turn_cannot_be_cited`, `::test_keys_after_digits_check_in_are_the_answer` |
| C4 | `packages/policy/tests/test_step.py::test_no_reply_after_check_in_ends_closed` (consent, change, digits), `::test_activity_resets_the_quiet_count`, `::test_check_in_cap` |
| C5 | `apps/voice/tests/test_twiml.py::test_speaker_events_are_subscribed` |
| C7 | `apps/voice/tests/test_transport.py::test_a_silent_vendor_gets_the_check_in_then_the_end` |
| C8 | review of the doc |

Existing tests whose expectations change (each because of D2 or D6, stated in the PR):
- `test_silence_three_times_ends_the_call`, renamed for two consecutive silences;
- `test_unclear_keeps_its_own_budget`, rewritten so it no longer relies on an `AgentUnclear` with no
  question to the model outstanding;
- any relay test that sends an empty final prompt expecting `Silence`.

## Out of scope

- Letting the caller interrupt the agent (`interruptible` stays `false`, R8).
- Changing the core's redial timing for a `failed` call.
- Acting on an opt-out, a callback request or a refusal said inside a presence turn. Agents don't
  record opt-out flags today at all (FINDINGS); fix both together.

## Open questions

- **Q117. Twilio's speaker events.** What exact message marks the agent stopping, for example
  `{"type": "info", "name": "agentSpeaking", "value": "off"}`? Does `tokens-played` give a more exact
  "line finished" signal? Are partial prompts sent by default? (D2's mid-sentence protection rests on
  them, or on `clientSpeaking`.) Is text sent during the welcome greeting queued? Confirm on the
  first live call. Until then, D3's estimate carries the clock.
- **Q118. The estimate.** Is 1 second plus 1 second per 12 characters, plus 3 seconds, right for the
  TTS voice in use? It only matters when Q117's message doesn't arrive.
- **Q119. Two gaps the review found, left open.**
  - **A split reply.** Only the first callee turn after a check-in is presence. Twilio could split
    "Yes. Yes, I'm here" into two final prompts. If the second lands before the re-ask plays, it
    could be grounded against the re-ask. The typed digits still gate verification. A fix needs
    Twilio's timing (Q117): for example, treat turns before the re-ask's agentSpeaking "off" as
    presence.
  - **A non-string field (pre-existing).** A `voicePrompt` or `digit` that is not a string still
    raises out of `run_relay`.

## Rulings (the author's calls, for Sami to check)

- **No reply after the digits check-in finishes with NR06 (keep every other answer), not a redial.**
  This matches spec 0043's Q79.
- **An empty final transcript is activity, not silence (D2).** Line noise shouldn't end a call.
- **An opt-out inside a presence turn is out of scope,** as above.

## Commands

```bash
cd packages/policy && uv run pytest -q
cd apps/voice && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages
```
