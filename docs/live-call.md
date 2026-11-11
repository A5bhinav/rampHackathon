# The first live test call

How to make Dialback place one real call to a team phone and reach Verified: what to set up,
what to run, what the person answering says, and what each failure looks like. It is demo beat 4
(`docs/README.md`). Every variable is defined in `docs/deploy.md`; this page only says which ones
matter for a call and in what order to do things.

A clean call goes: `demo change` opens a case and holds the payment, the Trust Ledger picks a
number it can prove, the core asks the voice service to call it, Twilio rings the team phone, the
AI discloses itself and asks seven questions, and code decides from the recorded answers. The
first matching block of rules in `packages/decision/src/dialback_decision/rules.py` decides:
Not verified rules (NV) first, then Needs review rules (NR), and Verified only when all twelve V
rules hold. Anything short of Verified leaves the hold on and a person decides (R7).

## 1. Once, before the first call

Twilio (Console):

- [ ] Accept the **Predictive and Generative AI/ML Features Addendum** in Voice Settings.
  ConversationRelay refuses to run without it (Twilio error 95250).
- [ ] `TWILIO_FROM_NUMBER` is a voice-capable number on this account.
- [ ] If the account is a **trial**:
  - the team phone must be a verified caller ID, or Twilio refuses to dial it (error 21219);
  - Twilio plays its own trial message and waits for a keypress before our disclosure.
  Upgrade before recording the video, or the first sentence the vendor hears is not ours (R8).

Railway (`docs/deploy.md`, "The voice service"):

- [ ] The voice service runs a build of main that has PR #79 (merge `683419b` or later). Before
  it, no live call could verify.
- [ ] `voice` has every live variable, `DIALBACK_VOICE` set to exactly `live`, and
  `ANTHROPIC_API_KEY`. Without the key the service still dials, but with the scripted placeholder
  agent instead of Claude.
- [ ] The team phone is in `DIALBACK_DIAL_ALLOWLIST` in E.164 form (for a US number, `+1` and ten
  digits, no spaces or dashes), the same form `demo load --phone` stores.
- [ ] `core` has `VOICE_URL` and `DIALBACK_RESOLVER=ledger`. Without `VOICE_URL` every call is
  refused. With `stub`, every call is refused because no number was selected.
- [ ] Optional: `CORE_PUBLIC_URL` on `core`, set before `demo reset` (the reset is what subscribes
  the webhooks). With it the phone rings seconds after `demo change`; without it, within
  `DIALBACK_POLL_SECONDS` (60 by default).

People:

- [ ] One teammate answers the team phone and follows section 5.
- [ ] A second person with the approver role releases afterwards. The clerk linked in `demo reset`
  entered the change, so they cannot release it (R5).

## 2. Pre-flight, each session (from a laptop)

Replace `<voice>` with the voice service's public domain.

```bash
curl -s https://<voice>/healthz
```

Expect `{"ok":true}`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<voice>/twilio/status/00000000-0000-4000-8000-000000000000
```

Expect `401`: the live routes are mounted, and an unsigned request is refused. `404` means the
service is on the text line. Either `DIALBACK_VOICE` is not exactly `live` (nothing is logged), or
a live variable is missing (the deploy log says "running the text line" and names what is
missing).

```bash
curl -s -i --http1.1 --max-time 5 -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" https://<voice>/twilio/relay/00000000-0000-4000-8000-000000000000 | head -1
```

Expect `HTTP/1.1 101 Switching Protocols`: the ConversationRelay socket opens through Railway.
The call id is unknown, so the service closes the socket at once, and nothing is called or
recorded. `404` here, after a `401` above, means the image has no WebSocket library
(`specs/0034-live-call-blockers.md`, B4). `403` goes with a `404` above: the library is there,
but the text line has no relay route (seen on the deployed service on Nov 8).

The pre-flight cannot see a missing `ANTHROPIC_API_KEY`. Check the voice deploy log for "wired
with the ScriptedAgent placeholder": if it is there, the key is missing.

## 3. Set up the demo (in the core service's shell)

```bash
dialback-core demo reset --link clerk=<the clerk's email>
```

```bash
dialback-ledger demo load --phone acme=+1<team phone>
```

```bash
dialback-core demo status
```

- The team phone is typed at run time, never committed.
- `--phone` takes effect only on the first `load` after a `reset`. To change the phone, reset
  again. Without `--phone`, the ledger picks Acme's fictional AR line, and the voice service
  refuses it as not on the allowlist.
- A reset also restarts the call limits. A number gets at most 3 calls in 30 days, counted per
  dataset, and a case gets at most 3 attempts. Every call the voice service accepted counts, even
  one that never rang. After three tries, reset and load again.

## 4. Place the call

Always in this order:

```bash
dialback-ledger demo request --vendor acme
```

```bash
dialback-core demo change --vendor acme
```

- The first command records Acme's change-request email in the ledger. The number selection
  rule needs a change request recorded before the case opens. Without it, no number is provable
  and no call is placed. The check that the person on the phone did not send the request also
  needs it.
- The second makes the bank change in the mock as the clerk. The core opens the case, holds the
  payment, and the call follows. Demo datasets call at any hour (`DIALBACK_DEMO_CALLING_WINDOW`
  defaults to `always`).

## 5. Answering the call (for the teammate on the phone)

Before the agent speaks, Twilio's answering-machine detection listens to how you answer and labels
the call. A call labeled `human` can reach Verified on its own (V03). So can one labeled `unknown`
(silence at pickup), as long as the digits were typed on the keypad: a machine can't type them
(spec 0080, Part B). With a `machine` label it goes to review, or is called again if Twilio's own
report of the call reaches the core first.

- **A greeting of up to about 4 seconds is fine**, from one short "Hello?" to "Acme accounts
  payable, this is Jane, how can I help you?". Then stay quiet: after about 1.2 seconds of quiet,
  detection decides and the disclosure starts a moment later. Talking for more than 4.5 seconds is
  labeled a machine.
- **Say something.** If you pick up and wait in silence, detection gives up after about 5 seconds
  with the label `unknown`, and only then does the disclosure start. The call can still verify,
  since the digits go in on the keypad.
- **On a trial account,** Twilio first plays its trial message and waits for a key. Press any key,
  then answer.

The label is in the voice service's deploy log on Railway, one line per call, written when Twilio
fetches the call's instructions. Search the log for `twiml fetch`; Railway shows the line with a
`INFO:dialback_voice.app:` prefix:

```
INFO:dialback_voice.app:twiml fetch for call <call id>: AnsweredBy=human MachineDetectionDuration=absent
```

`AnsweredBy` is Twilio's label (`human`, `machine_start`, `machine_end_beep`,
`machine_end_silence`, `machine_end_other`, `fax` or `unknown`), `absent` if Twilio sent none, or
`other` for anything else. `MachineDetectionDuration` is how long detection took, in
milliseconds. Expect `absent`: Twilio documents it only for asynchronous detection (spec 0080,
Q134). The call id is ours; the line never carries the phone number.

The agent speaks first and cannot be interrupted. Three habits keep the answers in the right
place:

- **Wait for each question to finish, then answer it, then stay silent.** After the consent and
  change answers the agent can pause for up to 8 seconds while the model reads the answer.
  Anything said during that pause is taken as the answer to the next question.
- **Say only the answer.** Nothing before or after it in the same breath.
- **If the agent says "Sorry, are you still there?", say anything** ("Yes, I'm here" is fine). The
  agent says "Thanks." and asks the question again; answer it as usual. Your reply to the check-in
  is never taken as an answer. The check-in comes after about 10 seconds of quiet, and no reply
  within another 10 seconds ends the call (`specs/0051-call-check-in.md`).

| The agent asks | You say | If you say something else |
|---|---|---|
| Disclosure, then "Is it okay to continue on this recorded line?" | "Yes." | A clear no ends the call with "we won't continue" (NR02). An answer the agent can't make out, like "Hello?" or "Sorry?", is asked once more ("Sorry, let me ask that again."); a second one ends the call, and the core calls again later while the case has attempts left |
| "Could you tell me the name of the company I've reached?" | "Acme Fasteners." | The name is not required (spec 0082, D7b): "Acme", a misheard name or another company's name doesn't block, and the person releasing sees the answer. An answer with a refusal word goes to review (NR03): "Not Acme, this is Globex", "We're a reseller for Acme", even "Not sure, Acme", "Sorry, I didn't catch that, Acme" or "Wrong number?". Say only the name |
| "Which department or team am I speaking with?" | "Accounts receivable." | Any answer works |
| "May I have your name?" | "Jordan Lee." | A name made only of words from the change email's senders (Dana Whitfield, "Acme Fasteners LLC Accounts Receivable") goes to review (NR04) |
| "And what's your role there?" | "AR clerk." | Any role works; a name and a role are both needed (V06) |
| "Has your company recently asked [customer] to change how you get paid?" | "Yes." | "I think so" goes to review (NR09 or NR14). An answer the agent can't make out is asked once more, as at consent |
| "For the account you'd like to be paid into, please use your phone's keypad to enter the last four digits, then press the pound key. To start over, press the star key." | Key `0` `0` `0` `1`, then `#` | See the keypad steps below |
| "Thank you, that's everything I needed." | Hang up | The line may cut off mid-sentence; the answers are already in |

The keypad steps (`specs/0043-keypad-only-digits.md`):

- **Type the four digits, then press `#`.** Only the keypad counts. Nothing goes in at the fourth
  key; if you forget `#`, the entry goes in 10 seconds after your last key.
- **Press `#` before you hang up.** Four digits typed without `#` are lost if you hang up within 10
  seconds: the call ends as dropped (failed), and the core calls again about 5 minutes later while
  the case has attempts left.
- **A slip? Press `*` and type all four again.** The agent says "Okay, let's start over." This
  costs nothing.
- **Need time to find the digits? Take it.** After 20 seconds with no key and no word, the agent
  says "Take your time. When you have the last four digits, enter them on your keypad, then press
  the pound key." Type them then. With nothing for another 20 seconds, the call finishes without
  digits and the case goes to review (NR06).
- **Do not say the digits.** Spoken digits are never recorded. The agent asks once for the keypad
  ("Please don't say the digits..."); a second time, the call finishes without digits and the case
  goes to review (NR06).
- **Exactly four.** An entry of any other length is asked once more; a second wrong-length entry
  finishes the call without digits (NR06).
- **Do not take the digits from the change email.** It says "ending 4821" (the ledger's demo
  seed, and the text line's scripted vendor), but the bank change `demo change` makes ends `0001`
  (`scripts/demo/seed.json`, `changes.acme`), and the call is checked against that. 4821 or 2222
  is a mismatch, and 4444 is Acme's old account: either way the outcome is Not verified (NV02 or
  NV03) and the case is blocked. The two seeds should agree; until they do, key 0001.

Pressure words are flagged by code and send the case to review (NR10): "urgent", "asap", "right
away", "immediately", "pay it today", "late fee" and similar.

### The detection check, on the first calls (spec 0080)

The detection settings (4.5 seconds of talking before "machine", 1.2 seconds of quiet before "they
finished") are educated guesses. The first calls test them. This check was written before any call.

- **Question:** is a real person labeled `human`, without the disclosure starting while they are
  still talking?
- **One call per case.** Run sections 3 and 4 again before each one.
  - (a) One short "Hello?", then silence until the agent speaks.
  - (b) A business-style greeting of about 4 seconds: "Acme accounts payable, this is Jane, how
    can I help you?"
  - (c) The phone's voicemail greeting (let the call go to voicemail). For information only:
    either label is fine. Run it last, then reset (section 3): a call that reaches voicemail is
    redialed later while the case has attempts left, so the team phone may ring again.
- **Record for each call:**
  - the `AnsweredBy` and `MachineDetectionDuration` values from the log line;
  - whether the account is a trial account. On a trial account, say your greeting only after
    pressing the key;
  - whether the disclosure started while you were still talking (yes or no).
- **Pass:** (a) and (b) are labeled `human`, and the disclosure did not start over them.
- **If (a) or (b) comes back `machine_start` or `unknown`, or the disclosure talked over you,**
  report it: the settings change, not the pass bar. One exception: on a trial account, retry an
  `unknown` once first, since the trial message may confuse detection.
- One call per case is a smoke check, not a measurement. Say so when you report the result.

## 6. After the call

```bash
dialback-core demo status
```

Acme's case should show `"state": "awaiting_release"` and `"outcome": "verified"`. The approver
releases it from the dashboard, and the state moves to `released` once the hold comes off.

**To try again**, after a call that ended in review or Not verified: the case is not called again
(only a dropped call, or one that ended after two unclear answers at consent or the change
question, or with no reply to a check-in before the keypad question, is called again), so run
sections 3 and 4 again (a new reset, load, request and change).

## When it goes wrong

When the voice service refuses a call, nothing rings, the hold stays on, and the case's timeline
on the dashboard says "The voice service checked the call and did not place it (...)".
`demo status` shows only an alert that `place_call` failed.

| What you see | Likely cause | Look at |
|---|---|---|
| Timeline: "No phone number for this vendor is provable, so no call was made" | `demo request` was skipped or ran after `demo change`, or `demo load` was skipped | Redo sections 3 and 4 in order |
| Refused: "no voice service is configured" | `VOICE_URL` is unset on core | Core variables |
| Refused: "no number was selected" | `DIALBACK_RESOLVER` is not `ledger` | Core variables |
| Refused: "the number is not on the team's dial allowlist" | The team phone is missing from `DIALBACK_DIAL_ALLOWLIST` or written differently, or `demo load` ran without `--phone` | Voice variables; section 3 |
| Refused: "the number has had its calls this month" or "the case has used its call attempts" | Three calls already in this dataset | Section 3: reset and load |
| The phone never rings, with no refusal | The voice service is on the text line (it accepts the call and dials nobody), or a trial account and an unverified number | Section 2's `401` check; the Twilio call log |
| It rings, then "an application error has occurred" | The TwiML fetch failed: a 401 (`DIALBACK_PUBLIC_BASE_URL` must be exactly the public `https://` address Twilio calls), a 404 (the voice service could not look the call up, for example its database is unreachable), or the AI/ML addendum is not accepted (95250) | The Twilio call log's error code, then the voice log |
| Silence after the disclosure | The socket failed (`DIALBACK_WS_BASE_URL` must be `wss://` on the same domain), or the voice service rejected the socket's setup (`setup_rejected`) | Voice log |
| "We won't continue" right after consent | Consent was read as a clear no | Voice log |
| "Sorry, are you still there?", then "Thanks for your time. Goodbye." | Nothing was said or pressed for about 10 seconds after the check-in. The call ends `failed`, and the core calls again after its wait (2 hours) while the case has attempts left | Voice log |
| "Sorry, let me ask that again", then "Thanks for your time. Goodbye." | Two answers to the consent question, or to the change question, could not be made out, or the model call failed twice (an invalid key, an API error, or a reply slower than 8 seconds). A missing key does not cause this. The call ends `failed`, and the core calls again after its wait (2 hours) while the case has attempts left | Voice log |
| The call finishes, but the case goes to review or is blocked | One rule decided it | The decision page names the rule. Common ones: NR01 (labeled a machine), NR03 (the company answer had a refusal word such as "not", "never" or "reseller"), NV02 or NV03 (wrong digits, blocked), NR10 (a pressure word), NR14 (an answer not found in the turn it cites) |

Not checked yet: a call on a real line. The steps above come from the code on main (Nov 8), not
from a completed call. Fix this page after the first one.
