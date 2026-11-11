# 0080: Answering-machine detection: fewer false "machine" labels, less dead air

**Owner:** Sami (Parts A and C, `apps/voice`; Part B is a proposal for Abhi, who owns `packages/decision`)
**Slice:** call and adversary  **Target day:** Tue Nov 10

## Goal

We dial with Twilio's answering-machine detection (AMD) on, in its synchronous mode
(`MachineDetection=Enable`), with Twilio's default settings. Twilio holds the answered call in
silence while it listens to the first stretch of talking. It labels the call `human`,
`machine_start`, `fax` or `unknown`, and only then fetches our TwiML. The label rides into the call
report as `answered_by`: `human`, `machine`, or absent for `unknown`.

The call itself ignores the label, because the relay always runs the person flow. The label
matters in two places:

- **The decision engine.** `NR01_no_live_person` (needs review) fires when `answered_by` is
  `machine`. `V03_live_person` (needed to verify) needs `answered_by` to be `human`. So only
  `human` lets a finished call verify on its own.
- **The status callback.** When a call labeled `machine_start` or `fax` completes, the status
  route also reports `result: machine` (`twilio/origination.py`, `status_result`). That report
  and the WebSocket's report share one idempotency key, so whichever lands first wins. The
  WebSocket's report usually wins, because it is sent before the socket closes. If it ever loses,
  the case counts the call as "no person" and redials.

Two problems follow:

1. **False "machine".** Talking for longer than 2.4 seconds is labeled a machine. Twilio's own guide
   puts business greetings at about 1.8 to 3 seconds. A person who answers "Acme accounts payable,
   this is Jane, how can I help?" can run over that.
   - Their whole call runs. They answer everything and type the right digits.
   - The case then goes to a person with the reason "No person answered the questions on this
     call", or is redialed if the status report lands first.
   - In synchronous mode Twilio returns `machine_start` as soon as the talking passes the
     threshold, while the person may still be talking. So on these calls the disclosure, which
     cannot be interrupted, may start over the end of their greeting.
2. **Dead air.** After someone says "Hello?", Twilio waits 1.2 seconds of quiet before deciding.
   Someone who picks up and waits for the caller to speak first gets 5 seconds of silence, then
   the label `unknown`. That also sends a finished call to review (NR99, with V03 unmet).

The errors are lopsided. A false "machine" or an `unknown` costs a review of a good call. A false
"human" on a voicemail costs almost nothing: a voicemail cannot type four digits on the keypad, so
`V08_digits_match` cannot pass. The call ends without answers and is redialed, as it would be
anyway. So the speech threshold should lean toward "human".

The plan:
- **Part A** raises the speech threshold, so fewer people are labeled machines, and logs each
  call's label so the values can be tuned from real calls.
- **Part B** proposes that an `unknown` label stop blocking verification when the person typed
  the digits.
- **Part C** then halves the silent-pickup wait.

## Decisions

### Part A (this PR, `apps/voice`)

1. **Detection stays synchronous.** For a `human` or `unknown` label, Twilio's wait is useful: the
   disclosure starts only after the person stops talking (or after silence), so they hear "AI" and
   "recorded line" instead of talking over them (R8).
2. **Three settings on the dial request,** as named constants in `twilio/origination.py`:

   | Twilio parameter | Plain meaning | Default | Sent |
   |---|---|---|---|
   | `MachineDetectionSpeechThreshold` | Talking shorter than this is a person; longer is a machine | 2400 ms | **4500 ms** |
   | `MachineDetectionSpeechEndThreshold` | Quiet after talking that counts as "they finished"; the dead air after "Hello?" | 1200 ms | **1200 ms** (unchanged, sent explicitly) |
   | `MachineDetectionSilenceTimeout` | Silence after pickup before giving up with `unknown` | 5000 ms | **5000 ms** (unchanged until Part C, sent explicitly) |

   `MachineDetectionTimeout` (30 s) is not sent. All the values are inside Twilio's allowed
   ranges.
   - **4500 ms goes above Twilio's advice on purpose.** Twilio's guide suggests 1800 to 3000 ms
     for businesses and says voicemail greetings are usually longer than 3 seconds. Its advice
     weighs both errors equally; ours are lopsided.
     - Above the guide, voicemails of 3 to 4.5 seconds become a false "human". That is cheap.
     - People whose greetings run 2.4 to 4.5 seconds now finish talking and are labeled `human`,
       and they hear the disclosure after they stop. That is better for R8 too.
   - **The end-of-speech wait stays at 1200 ms.** A lower value would save about 0.4 seconds, but
     it would end a person's greeting at a natural pause. They would be labeled `human` and the
     disclosure would start while they are still talking, on a call that can verify with no
     review. Twilio's guide goes the other way (1400 to 1500 ms). Changed after the builder
     check: the first draft lowered it to 800 ms.
   - **The values are educated guesses, not measurements.** The first live calls test them (the
     check below).
3. **One log line per TwiML fetch.**
   - **Where:** in the `/twilio/twiml/{call_id}` route, right after the Twilio signature is
     verified and before the call lookup. A signed fetch for an unknown call (404) still logs.
     A forged fetch (401) logs nothing.
   - **What:** on logger `dialback_voice.app`, at INFO:
     `log.info("twiml fetch for call %s: AnsweredBy=%s MachineDetectionDuration=%s", call_id, answered, duration)`.
     Here `call_id` is our path UUID, not Twilio's CallSid.
   - **`answered`:** the `AnsweredBy` value when it is one of Twilio's documented values (`human`,
     `machine_start`, `machine_end_beep`, `machine_end_silence`, `machine_end_other`, `fax`,
     `unknown`). It is `absent` when the field is missing or blank, and `other` for anything else.
   - **`duration`:** the `MachineDetectionDuration` value when `re.fullmatch(r"[0-9]{1,6}", v)`
     matches (ASCII digits only, no trailing newline). It is `absent` when missing or blank, and
     `other` for anything else.
   - "Blank" means the empty string. A whitespace-only value is `other`, like any other value
     outside the lists (clarified after review; the code already behaved this way).
   - The line never carries a phone number, the call token, the CallSid or any other parameter.
     Both values are treated as data (R9).
   - Twilio documents `MachineDetectionDuration` for the asynchronous callback, but does not say
     the synchronous fetch carries it. Expect `absent` until a live call shows otherwise (Q134).
4. **Nothing else changes:** the TwiML, `_answered_by`, the status callback, the call report and
   the decision rules all stay as they are.

### Check for the first live calls (written before any call)

The agent never places calls. A teammate places them by hand, from the team allowlist.

- **Question:** do the new settings label a real person `human`, without the disclosure starting
  while they are still talking?
- **Record per call:**
  - the `AnsweredBy` and `MachineDetectionDuration` values from the log line;
  - whether the account is a trial account. On a trial account, Twilio plays a message and waits
    for a key, so say "Hello?" only after pressing the key.
  - whether the disclosure started while you were still talking (yes or no).
- **Cases:**
  - (a) one short "Hello?", then silence;
  - (b) a business-style greeting of about 4 seconds ("Acme accounts payable, this is Jane, how
    can I help you?");
  - (c) a phone with its voicemail greeting on (informational: either label is fine);
  - (d) for Part C only: pick up and say nothing.
- **Pass:** (a) and (b) are labeled `human`, and the disclosure did not start over them.
- **Revisit the values, not the bar,** if (a) or (b) comes back `machine_start` or `unknown`, or
  the disclosure talked over them. Exception: an `unknown` on a trial account is retried once
  first, since the trial message may confuse detection. That is a confounder, not a result.
- **Sample:** one call per case. This is a smoke check, not a measurement, and the result says so.

### Part B (proposal for Abhi; not built here)

Typing four digits on the keypad, in answer to the question, proves a live person is on the line
better than a two-second audio check does. A voicemail, fax or phone menu cannot do it.

**The proposal covers only the `unknown` label** (`answered_by` absent).
- `unknown` comes from silence at pickup, so the disclosure did not start over anyone.
- A `machine_start` label is different. In synchronous mode, Twilio returns it while the far end
  may still be talking, so the disclosure may have been talked over. NR01 keeps sending those
  calls to a person (R8). Part A's higher threshold is what reduces them.

**Proposed change.** NR01 is unchanged. V03 becomes:

| Rule | Today | Proposed |
|---|---|---|
| `V03_live_person` | result is `answers_recorded` **and** label is `human` | result is `answers_recorded` **and** (label is `human`, **or** label is absent **and** `typed`) |

**What `typed` means.** At least one `account_last4` answer on the call was entered on the keypad
(`capture_method` is `keypad`), and `last_four_result` is set and is not `not_provided`.
- Today only `policy.step` guarantees that digits come from the keypad: spoken digits are
  refused (spec 0043).
- The core computes `last_four_result` from any `account_last4` answer, whatever its
  `capture_method`, and the answers schema allows `speech`.
- So Part B needs a precondition. Either the core passes a `digits_typed` fact computed from
  `capture_method`, or it refuses spoken `account_last4` answers. Abhi's call (Q133).

**Effects** (this call's own result; an earlier consented denial still decides first, as today):

| Call | Today | Proposed |
|---|---|---|
| Label absent (`unknown`); every other check passes; the digits match | needs review (NR99, V03 unmet) | verified |
| Label absent; the digits match the old account, or don't match | not verified (NV03 or NV02) | unchanged |
| Label absent; no digits typed | needs review | needs review |
| Label `machine` (any digits) | needs review (NR01), or a redial if the status report lands first | unchanged |
| Label `human` | unchanged | unchanged |

**Why no verified outcome becomes less safe.**
- **Fraud:** `V08_digits_match` needs `match`, so every newly verified call passed V08 and every
  other V rule, exactly as a `human`-labeled call must today. The label never protected against
  fraud anyway: a fraudster on the line is a person too. The defense against them is the number
  we chose (R2) and the digits (V08).
- **R8:** an `unknown` label means silence at pickup, so there was no talk-over.
- **No status-report race:** the status route reports nothing for an `unknown` call.

**What it touches.**
- The `V03` predicate and its "not met" text.
- `ENGINE_VERSION` (`engine.py`), and spec 0006's V03 row.
- Engine tests that pin V03 as unmet for an absent label.
- No contract changes: `answered_by`, `last_four_result` and `capture_method` already exist.
- The lab cannot exercise this case. Its text line ends a `machine` persona at connect, and its
  personas never produce `unknown`. So it needs engine unit tests, not lab truth.

### Part C (after Part B merges, `apps/voice`)

Lower `MachineDetectionSilenceTimeout` from 5000 to **2500 ms**. Someone who picks up and waits
silently then hears the disclosure after about 2.5 seconds instead of 5.
- It produces more `unknown` labels, which only costs nothing once Part B lands. Until then it
  would send more good calls to review, so it waits.
- **Risk:** someone who waits about 2.5 seconds and then says "Hello?" may overlap the first words
  of the disclosure. Check case (d) and the talk-over question cover this.
- It gets its own small PR.

## Considered and rejected

- **Asynchronous detection (`AsyncAmd=true`).** It removes the dead air entirely, because the TwiML
  runs at pickup and the label arrives later through a callback. But the disclosure would start
  while the person is still saying hello or giving their greeting, and since it cannot be
  interrupted, they may talk over "AI" and "recorded line" (R8). It also needs a new callback
  route and a race between that callback and the call report.
- **Lowering the end-of-speech wait** (the first draft's 800 ms). See Part A, D2.
- **Letting a typed entry also override a `machine_start` label** (the first draft's Part B). The
  label says the disclosure may have been talked over (R8).
- **No detection.** Same talk-over problem, and the status callback loses the `machine` result.
- **Leaving a proper voicemail** (`DetectMessageEnd`, so the message lands after the beep). Today
  the relay plays the person flow to a voicemail. A separate change.

## Contract touched

None.

## Acceptance

Part A:

- [ ] A1. The dial request sends `MachineDetection=Enable`, `MachineDetectionSpeechThreshold=4500`,
  `MachineDetectionSpeechEndThreshold=1200` and `MachineDetectionSilenceTimeout=5000`, and sends no
  `AsyncAmd` and no `MachineDetectionTimeout`.
- [ ] A2. The values are named constants in `twilio/origination.py`, each with its reason in a comment.
- [ ] A3. A signed TwiML fetch logs exactly one INFO line on `dialback_voice.app` in D3's format,
  with our path call id, the `AnsweredBy` value and the duration. The TwiML returned is the same as
  before. A signed fetch for an unknown call id still logs its line and still returns 404.
- [ ] A4. Values outside D3's lists are logged as `other`, and missing or blank ones as `absent`.
  This holds for `AnsweredBy` values such as `Human` or `human\n`, and for durations such as
  `123\n`, `-5`, `1234567` and non-ASCII digits. The line never contains the dialed number, the
  `call_token` or the CallSid.
- [ ] A5. A forged (unsigned) TwiML fetch logs no detection line and is still rejected with 401.
- [ ] A6. Unchanged: the TwiML, `_answered_by`, the status callback mapping and the call report.
  The existing tests pass without edits.
- [ ] A7. `docs/live-call.md` section 5 is updated. It says a greeting up to about 4 seconds is
  fine, where to read the log line, and carries the check above (cases (a) to (c)) for the
  teammate placing the first calls.

Part B and Part C have no acceptance items here. Part B is Abhi's to accept, specify and build.
Part C is its own PR, and adds case (d).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| A1, A2 | `apps/voice/tests/test_origination.py::test_dial_request_carries_the_detection_settings` |
| A3 | `apps/voice/tests/test_twiml.py::test_twiml_fetch_logs_the_detection_result` |
| A3 | `apps/voice/tests/test_twiml.py::test_twiml_fetch_for_an_unknown_call_still_logs` |
| A4 | `apps/voice/tests/test_twiml.py::test_unexpected_detection_values_are_logged_as_other_or_absent` |
| A5 | `apps/voice/tests/test_twiml.py::test_a_forged_twiml_fetch_logs_no_detection_line` |
| A6 | the existing `apps/voice` suite, unedited |

## Out of scope

- Part B's rule change and Part C's lower silence timeout (above).
- The status-report race for `machine` calls. It fails safe (a redial), and it is noted here and
  in Q133.
- Making the relay play the voicemail line on a `machine` label.
- Measuring the delay from the end of detection to the first audio (TwiML fetch, socket, speech).

## Open questions

- **Q133 (Abhi):**
  - Accept Part B? If so, which precondition for `typed`: a `digits_typed` fact computed by the
    core from `capture_method`, or the core refusing spoken `account_last4` answers?
  - Separately: should the status route stop reporting `machine` for a call whose WebSocket was
    verified, so the race can't turn an answered call into a redial? That is an `apps/voice`
    change; Sami would build it.
- **Q134 (Sami, first live call):** does the synchronous TwiML fetch carry
  `MachineDetectionDuration`? Twilio documents it only for the asynchronous callback. Do check
  cases (a) and (b) pass?
- **Q135 (Sami, first live call):** does a 4.5-second speech threshold label many real voicemails
  `human`? That is cheap, but it decides whether the voicemail line (out of scope) is worth
  building.

## Commands

```bash
cd apps/voice && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts corpus
python3 scripts/ci/boundaries.py
```
