# The first live test call

How to make Dialback place one real call to a team phone and reach Verified: what to set up,
what to run, what the person answering says, and what each failure looks like. It is demo beat 4
(`docs/README.md`). Every variable is defined in `docs/deploy.md`; this page only says which ones
matter for a call and in what order to do things.

A clean call goes: `demo change` opens a case and holds the payment, the core asks the voice
service to call the number the Trust Ledger picked, Twilio rings the team phone, the AI discloses
itself and asks seven questions, and code decides from the recorded answers. Verified needs all
twelve V rules in `packages/decision/src/dialback_decision/rules.py`; any one unmet sends the case
to a person, and the hold stays (R7).

## 1. Once, before the first call

Twilio (Console):

- [ ] Accept the **Predictive and Generative AI/ML Features Addendum** in Voice Settings.
  ConversationRelay refuses to run without it (Twilio error 95250), and the call ends silently.
- [ ] `TWILIO_FROM_NUMBER` is a voice-capable number on this account.
- [ ] If the account is a **trial**:
  - the team phone must be a verified caller ID, or Twilio refuses to dial it (error 21219);
  - Twilio plays its own trial message and waits for a keypress before our disclosure.
  Upgrade before recording the video, or the first sentence the vendor hears is not ours (R8).

Railway (`docs/deploy.md`, "The voice service"):

- [ ] The voice service runs a build of main that has PR #79 (merge `683419b` or later). Before
  it, no live call could verify.
- [ ] `voice` has every live variable, `DIALBACK_VOICE=live`, and the team phone in
  `DIALBACK_DIAL_ALLOWLIST`, written exactly as E.164 (for a US number, `+1` and ten digits, no
  spaces).
- [ ] `core` has `VOICE_URL` and `DIALBACK_RESOLVER=ledger`. Without `VOICE_URL` every call is
  refused. With `stub`, every call is refused `number_not_selected`.
- [ ] Optional: `CORE_PUBLIC_URL` on `core`. With it the phone rings seconds after `demo change`;
  without it, within `DIALBACK_POLL_SECONDS` (60 by default).

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
service is on the text line because a live variable is missing; its deploy log names which one
("running the text line").

```bash
curl -s -i --http1.1 --max-time 5 -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" https://<voice>/twilio/relay/00000000-0000-4000-8000-000000000000 | head -1
```

Expect `HTTP/1.1 101 Switching Protocols`: the ConversationRelay socket opens through Railway.
The call id is unknown, so the service closes the socket at once, and nothing is called or
recorded. `404` here, after a `401` above, means the image has no WebSocket library (spec 0034,
B4).

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
  again.
- A reset also restarts the call limits. A number gets at most 3 AI calls in 30 days, counted per
  dataset, and a case gets at most 3 attempts. After three test calls, reset and load again, or the
  next call is refused `number_cap_reached`.

## 4. Place the call

```bash
dialback-ledger demo request --vendor acme
```

```bash
dialback-core demo change --vendor acme
```

The first command adds Acme's change-request email to the ledger, so the number in it is
rejected (R2). The second makes the bank change in the mock as the clerk. The core opens the case,
holds the payment and calls. Demo datasets call at any hour (`DIALBACK_DEMO_CALLING_WINDOW`
defaults to `always`).

## 5. Answering the call (for the teammate on the phone)

Answer with one short "Hello?", then stay quiet. Twilio's answering-machine detection decides from
that greeting. A long greeting can sound like voicemail, and the call then cannot verify (V03).
On a trial account, press any key after Twilio's trial message.

The agent speaks first and cannot be interrupted, so wait until it stops before answering. Give
one answer per question.

| The agent asks | You say | Why |
|---|---|---|
| Disclosure, then "Is it okay to continue on this recorded line?" | "Yes." | Consent must be the first answer, on its own (V04) |
| "Could you tell me the name of the company I've reached?" | "Acme Fasteners." | Every word of the vendor's name, without "LLC". "Acme" alone is not a match (V05) |
| "Which department or team am I speaking with?" | "Accounts receivable." | Any answer |
| "May I have your name?" | "Jordan Lee." | The contact on file. Not Dana Whitfield, who sent the change email, or any name made only of words in its thread's senders (NR04) |
| "And what's your role there?" | "AR clerk." | A name and a role are both needed (V06) |
| "Has your company recently asked [customer] to change how you get paid?" | "Yes." | A plain yes. "I think so" reads as unsure (NR09) |
| "For the account you'd like to be paid into, what are the last four digits?" | "Zero, zero, zero, one." or key `0001` | The new account in `scripts/demo/seed.json` (`changes.acme`) |
| "Thank you, that's everything I needed." | Hang up | |

Traps:

- **Do not read the digits from the change email.** It says "ending 4821" (the ledger's demo seed,
  and the text line's scripted vendor), but the bank change `demo change` makes ends `0001`
  (`scripts/demo/seed.json`), and the call is checked against that. Saying 4821 is a mismatch, Not
  verified (NV02). Saying 2222 or 4444, Acme's old accounts, is also Not verified (NV03). The two
  seeds should agree; until they do, say 0001.
- **Use one form of your name all call.** "Jordan" and later "Jordan Lee" count as two people
  (V09).
- **No pressure words.** Code flags "urgent", "asap", "right away", "immediately", "pay it today",
  "late fee" and similar (NR10).
- Do not ask what is being checked (NR12), offer another number (NR13), or ask not to be called
  again (NR11).

## 6. After the call

```bash
dialback-core demo status
```

Acme's case should show `"state": "awaiting_release"` and `"outcome": "verified"`. The approver
releases it from the dashboard, and the state moves to `released` once the hold comes off.

## When it goes wrong

When a call is refused before dialing, nothing rings, the case goes to a person with the hold on,
and its event log says `pre-dial check refused: <code>`.

| What you see | Likely cause | Look at |
|---|---|---|
| Refused `voice_not_configured` | `VOICE_URL` is unset on core | Core variables |
| Refused `not_on_dial_allowlist` | The team phone is missing from `DIALBACK_DIAL_ALLOWLIST`, or written differently from `--phone` | Voice variables |
| Refused `number_not_selected` | `DIALBACK_RESOLVER` is not `ledger`, or `demo load` ran without `--phone` | Core variables, then reset and load |
| Refused `number_cap_reached` or `attempt_cap_reached` | Three calls already in this dataset | Section 3: reset and load |
| The phone never rings, with no refusal | The voice service is on the text line (it accepts the call and dials nobody), or a trial account and an unverified number | Voice deploy log; the Twilio call log |
| It rings, then "an application error has occurred" or silence | The TwiML fetch got 401 (`DIALBACK_PUBLIC_BASE_URL` must be exactly the public `https://` address Twilio calls); the AI/ML addendum is not accepted (95250); or the socket failed (`DIALBACK_WS_BASE_URL` must be `wss://` on the same domain) | The Twilio call log's error code, then the voice log |
| The agent says "we won't continue" right after consent | Consent was read as no. A model error records no (R7), so check `ANTHROPIC_API_KEY` | Voice log |
| The call finishes, but the case goes to review | One rule was unmet | `demo status` or the decision page names it. Common ones: V03 (not detected as a person), V05 (partial company name), V08 (wrong digits), V09 (two forms of the name), NR10 (a pressure word) |

Not checked yet: a call on a real line. The steps above come from the code on main (Nov 8), not
from a completed call. Fix this page after the first one.
