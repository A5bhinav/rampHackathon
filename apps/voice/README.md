# apps/voice: the voice service

**Owner: Sami. Partner reviewer: Inesh.** Part of the call-and-adversary slice with
`packages/policy`, `evals` and the `voice` and `lab` schemas.

A FastAPI WebSocket server on Railway. Twilio ConversationRelay sends the caller's words as
text; the service sends back text to speak. All dialogue logic lives in
`packages/policy` (`policy.step(state, event) -> Step`), so this service is the transport:
Twilio in, policy out, answers to the core.

## Job

Make a short, disclosed call to the chosen number and record what the vendor says, with no
way to change the outcome.

## Interfaces

| Direction | Interface |
|---|---|
| In, from the core worker | `POST /calls {case_id, attempt}`. The voice service reads the chosen number from the database itself, so nothing in the request can change who gets called. Contract: `packages/contracts/openapi/internal.yaml`. |
| In, from Twilio | The ConversationRelay WebSocket (verify `X-Twilio-Signature`, the one-time `call_token`, and that the dialed number is the chosen one); status callbacks for calls that never connect (no answer, busy, failed), verified the same way. |
| Out, to the core | `call_ended` with the recorded answers, or a non-connect result. Body: `CallEndedBody` in `packages/contracts/schemas/evidence-event.schema.json`; the `answers` object is `packages/contracts/schemas/call-answers.schema.json`, which you own. |
| Out, to Postgres | `voice.calls` and `voice.call_answers` (your schema). |

## How a call runs

1. Before dialing, code checks: the case is unfinished and held; the number is the Trust Ledger's pick; the number has not opted out; the vendor is inside its calling window (Monday to Friday, 9:00 to 16:30 vendor local time); the caps are not reached (3 attempts per case, 3 AI calls per number per 30 days). For the demo, the number must also be on the team allowlist, and the guard has its own test.
2. Code mints a one-time `call_token` tied to the case, the attempt and the number, and passes it to Twilio as a `<Parameter>`. When Twilio connects, the adapter checks the signature, the token, and the dialed number. The model never sees a case ID.
3. The disclosure is fixed text from code, played as the `welcomeGreeting` with interruptions off, so it always plays in full: an AI assistant calling for the customer's AP team, on a recorded line, with a callback number. Then a consent question; a "no" ends the call (R8).
4. A small state machine owns the order of questions: the right company, the right department, the person's name and role, whether they asked to change how they are paid, the last four digits of the account they want paid, and a close that reveals nothing. The model only words the questions.
5. A speech guard checks every reply before it is spoken: it blocks runs of three or more digits (except a read-back of digits the callee just said), the words "verified", "approved" and "match", amounts and bank names, and speaks a fixed fallback line instead (R4).

## Tools (the model's whole action space)

| Tool | Does | Limits |
|---|---|---|
| `record_answer(slot, value, turn_id)` | Saves what the callee said | Slot names from a fixed list; each value checked against its slot's type; must cite the transcript turn; returns only `{"ok": true}` |
| `schedule_callback(window)` | Books a callback | Same number only, so there is no number field; code enforces the window and caps |
| `end_call(reason)` | Ends the call | Reason from a fixed list |
| `press_keys(digits)` | Picks a phone-menu option (menu state only) | 1 to 3 keys from 0-9, * and #; 5 uses |

Arguments are validated strictly with no extra fields. An unknown tool or a bad argument is
logged as a policy violation and the question is asked again; a second one ends the call
and sends the case to needs_review. Code turns tool calls into the `answers` object; the AI
emits no events (invariant I4).

**What the model never sees:** the new or old bank details, the expected digits, the request's
content or date, the number's lineage. Its context holds the customer's name, the vendor's
name, the AP callback number and the script. The callee's words are passed in as quoted
data, never as instructions (R9).

## Edge cases

Voicemail gets one fixed, generic message per case (no bank, change or amount), then a
retry. A phone menu ends the call and sends the case to needs_review for Nov 10; pressing
through a mock menu is a stretch goal. A dropped call gets one redial within five minutes,
and its answers are never combined with the redial's. "Call my cell instead" is recorded and
never dialed.

## Setup and fallback

Twilio's free trial blocks ConversationRelay, so upgrade the account on day 1 and buy two
numbers. If live calls are not reliable by the Nov 4 go/no-go (disclosure plays in full, four
digits captured in at least four of five tries, clean end, decision lands in the dashboard),
the same script and tools move to Retell with the case ID kept out of the prompt in Retell's
call metadata; the lab stays on the text line either way.

## Call compliance for the demo (all of it, every call)

1. A dial allowlist in code: the three teammates' phones and team-owned test numbers only.
2. Written consent from each teammate and anyone else who answers (a judge), covering AI calls, recording, processing by our vendors and use in the video. Records stay out of the repo.
3. Disclosure first (AI, recording, the customer's name, a callback number), then a consent question.
4. Treat every call as needing all-party consent (California).
5. Nothing reaches anyone outside the team: no voicemails, phone-menu tests or lab audio.
6. Never market anything on a call.
7. At the live final, a judge playing the vendor uses a team phone after a spoken OK.

## Data you own

`voice.calls` (case_id, contact_point_id, provider_call_id, status, recording_ref,
transcript_ref redacted, agent_version) and `voice.call_answers` (call_id, slot, value,
tool_call_id, captured_at). Put them in `supabase/migrations/voice/`; `scripts/db/apply.sh`
applies that folder after `app`. Recordings and raw transcripts go to a private bucket with
short-lived signed links; each recording's SHA-256 goes into the evidence body.

## Your days

| Day | Deliverable |
|---|---|
| Sat Oct 31 | Twilio upgraded, two numbers bought, one test ConversationRelay call to a teammate; scenario format and oracle rules |
| Sun Nov 1 | Text phone line with a scripted vendor; the lab runner runs 3 scenarios and writes a scorecard |
| Mon Nov 2 | 40 dev scenarios; deterministic checks |
| Tue Nov 3 | Voice agent v1: disclosure, tools, last-four capture; first live call between team phones |
| Wed Nov 4 | Go/no-go; attacker personas on a second model family for 5 classes |
| Thu Nov 5 | Voicemail, no answer, opt-out; 150 attacks and 30 legitimate changes; nightly full runs start |
| Fri Nov 6 | Fixes from the first full runs |
| Sat Nov 7 | 300 attacks and 60 legitimate changes complete; 10 to 20 audio spot checks |
| Sun Nov 8 | Final scorecard on the frozen commit; held-out results unsealed |
| Mon Nov 9 | Live-attack beat rehearsed |
| Tue Nov 10 | Check the scorecard and video links |

## Tests you owe

Unit: the speech guard, tool-argument validation, the dial allowlist guard. Script adherence
checked in code: disclosure in the first turn, no account digits spoken, no new number
accepted, opt-out ends the call. Contract: `call-answers.schema.json` examples validate in
both languages.

## Start here

1. Write `packages/contracts/schemas/call-answers.schema.json` first: the slots, their allowed values, and the `turn_id` citation. Abhi's `decide()` and the evidence schema reference it.
2. Write `supabase/migrations/voice/0001_voice.sql` and run `scripts/db/test.sh`.
3. Scaffold `src/dialback_voice/`: `main.py` (POST /calls, the WebSocket route, status callback), `twilio/relay.py` (ConversationRelay adapter), `guards.py` (pre-dial checks and allowlist), `tokens.py` (call_token mint and verify).
4. `DIALBACK_VOICE=text|live` from one settings module; text is the safe default.
