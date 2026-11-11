# 0094: The closing line plays before the call ends

**Owner:** Sami (`apps/voice`)  **Slice:** call and adversary  **Target day:** Tue Nov 10

## Goal

At the end of a call the agent says its closing line: "Thank you, that's everything I needed. Have a good day.",
"Thanks for your time. Goodbye.", or the line after a refusal or a callback request. Today the
caller rarely hears all of it.

**Why it is cut off.**
1. `run_relay` sends the line as a `text` token and returns at once.
2. `run_relay_call` reports the call to the core.
3. The WebSocket route returns, and the socket closes while Twilio is still speaking.

Twilio's ConversationRelay docs say a WebSocket that disconnects unexpectedly ends the call with a
`failed` status (error 64105). The documented way to end a session is the `end` message.

After this change:
1. The relay reports first, so the decision is not delayed.
2. It then waits until the closing line has played.
3. It then sends `{"type": "end"}`, so Twilio ends the session, and the call with it, on purpose.

## Decisions

1. **Report, then wait, then end.**
   - `run_relay` takes an optional async `on_end(result)` and awaits it for every result it
     returns. That includes a rejected setup and a call that reached its End.
   - `run_relay_call` passes the report it already builds (`report_body`, `with_connect_facts`,
     `report_ended`).
   - So the report goes out before any waiting, and still before the socket closes. That keeps
     spec 0080's race order, where the WebSocket's report lands before the status callback's.
2. **What "has played" means.** The wait ends at the first of these:
   - Twilio's agentSpeaking "off" after an "on" since the line was sent, plus `END_TAIL_SECONDS`
     (0.5 s), so the last syllable isn't clipped by network delay;
   - the speech estimate for the lines sent (spec 0051, D3: 1 s, plus 1 s per 12 characters, plus
     3 s). For "Thanks for your time. Goodbye." that is about 6.5 s;
   - the socket dropping (the caller hung up first).

   This uses the same "off after an on since the send" signal the quiet clock already uses
   (spec 0051), with the estimate as the cap when Twilio sends no speaker events (Q117).
3. **Nothing during the wait counts.** Prompts and keys that arrive during the wait are read and
   dropped. They are not policy events, and nothing is recorded: the call has already ended.
4. **`end` only on a live socket.** `{"type": "end"}` is sent only when the wait ended without the
   socket failing. A socket that failed during the wait gets nothing more. A failed send of `end`
   is ignored, because the call is over either way.
5. **A failed report still ends the call properly.** The wait and `end` run in a `finally`. The
   report's error then propagates as it does today.
6. **No closing line means no wait.** An End that spoke nothing (a dropped socket, say) returns
   at once, as today. So does a rejected setup: it is closed at once with nothing spoken.
7. **A hang-up at the closing line still reports.** If sending the closing line fails, the caller
   hung up as it went out. The result is reported through `on_end` as usual, with no wait and no
   `end`. Before this, the send's error left the call unreported until the core's
   `call_timeout`. A send that fails mid-call still propagates, as before. Added after the review.
8. **One log line says what ended the wait.** `closing line: the wait ended (agent_stopped |
   estimate | dropped)`, on logger `dialback_voice.twilio.relay`, with no content and no number. It
   lets the first live calls tell a missing agentSpeaking (Q117) from a slow end. Added after the
   review.
9. **The text line is unchanged.** `transport.run_call` has no audio.

## Contract touched

None.

## Acceptance

- [ ] G1. When a call reaches its End with a closing line, the relay sends the line, then awaits
  `on_end` with the result, then waits for the line to play (D2), then sends `{"type": "end"}`.
  `run_relay` returns the same result as before.
- [ ] G2. The report is not held back by the wait: `on_end` runs before the wait starts.
- [ ] G3. With agentSpeaking "on" then "off" after the line is sent, `end` follows the "off" by
  `END_TAIL_SECONDS`, well before the estimate. With no speaker events, `end` comes at the
  estimate.
- [ ] G4. Prompts and keys during the wait change nothing: the result, the answers and the
  transcript are what they were at the End.
- [ ] G5. A socket that fails during the wait ends the wait at once, and no `end` is sent.
- [ ] G6. A report that raises still gets the wait and `end`, and the error then propagates out of
  `run_relay_call`.
- [ ] G7. A rejected setup is closed at once and reported, with nothing spoken and no `end`. An End
  with no closing line sends no `end` and doesn't wait.
- [ ] G8. `docs/live-call.md` says the agent now finishes its closing line and hangs up itself,
  and carries the check below.
- [ ] G9. If sending the closing line fails, the call is still reported, with no wait and no `end`
  (D7).
- [ ] G10. The wait logs one line naming what ended it: `agent_stopped`, `estimate` or `dropped`
  (D8).

### Check for the first live calls (written before any call)

- **Question:** do you hear the whole closing line before the line drops?
- **Cases:**
  - (a) a full call that ends with "Thank you, that's everything I needed. Have a good day.";
  - (b) a refusal at consent, which ends with the "we won't continue" line.
- **Record per call:** the `closing line: the wait ended (...)` log line.
- **Pass:** both lines are heard to the end, and the agent ends the call itself, without the
  caller hanging up.
- **How long after the line to expect the drop:**
  - about 0.5 s when the log says `agent_stopped`;
  - up to about 4 s when it says `estimate`, which means Twilio sent no speaker events (Q117).

  Neither is a failure. Clarified after the review: the first draft's "within about 2 s" would
  fail on every call with no speaker events, without saying why.
- **If a line is cut off,** revisit `END_TAIL_SECONDS` or the estimate, not the bar. One call per
  case: this is a smoke check.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| G1, G2 | `apps/voice/tests/test_relay.py::test_the_closing_line_is_reported_then_played_then_ended` |
| G3 | `apps/voice/tests/test_relay.py::test_end_follows_the_agent_stopping_or_the_estimate` |
| G4 | `apps/voice/tests/test_relay.py::test_input_while_the_closing_line_plays_changes_nothing` |
| G5 | `apps/voice/tests/test_relay.py::test_a_drop_while_the_closing_line_plays_sends_no_end` |
| G6 | `apps/voice/tests/test_relay.py::test_a_failed_report_still_plays_the_closing_line` |
| G7 | `apps/voice/tests/test_relay.py::test_no_closing_line_or_a_rejected_setup_sends_no_end` |
| G2, D2 | `apps/voice/tests/test_relay.py::test_a_slow_report_does_not_move_the_end` (fails if the estimate is armed after the report) |
| G9 | `apps/voice/tests/test_relay.py::test_a_hang_up_at_the_closing_line_still_reports` |
| G10 | `apps/voice/tests/test_relay.py::test_the_wait_logs_what_ended_it` |

The test socket keeps the spoken `text` tokens in `sent`, as before, and any other message (the
`end`) in `control`. The existing tests read only `sent`.

## Out of scope

- A proper voicemail message on a `machine` label (spec 0080, out of scope there too).
- `<Connect action=...>` and `handoffData`: nothing follows the session, so the call ends when it
  does.

## Open questions

- **Q160 (Sami, first live call):** does `end`, sent 0.5 s after agentSpeaking "off", ever clip the
  last word? Twilio doesn't document whether `end` waits for speech that is still playing. Q117
  (does agentSpeaking arrive at all) also bears on this: without it, the wait is the estimate.

## Commands

```bash
cd apps/voice && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts corpus
python3 scripts/ci/boundaries.py
```
