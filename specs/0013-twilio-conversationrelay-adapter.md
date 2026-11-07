# 0013: The Twilio ConversationRelay adapter

**Owner:** Sami  **Slice:** call and adversary  **Target day:** Fri Nov 7 (logic pre-freeze; live calling gated by the Nov 4 go/no-go and the Twilio account)

## Goal

A real phone call runs the same `policy.step()` the text line already drives (spec 0012), over a Twilio ConversationRelay WebSocket instead of `ScriptedVendor`. The adapter dials the Trust Ledger's pick, plays the disclosure first (R8), maps the call's words and keypresses into policy events, speaks back only what the guard has cleared (R4), and reports the result to the core (R3, R7, R9). The model never learns the case id, the bank details, or what is being checked.

The adapter logic is testable with no live Twilio and no model key: a fake WebSocket and `ScriptedAgent` drive it, exactly as the text line is tested (Q43). Turning it on against real numbers is an ops step behind the go/no-go, not part of this spec.

## Contract touched

None. The report already exists (`internal.yaml` `reportCallEnded`, spec 0012); the ConversationRelay WebSocket is Twilio's shape, not ours. `call_token` mint/verify already exist (`tokens.py`).

## Decisions (proposed; Sami to confirm)

1. **New code, all in `apps/voice`:** `twilio/relay.py` (the ConversationRelay adapter: WS loop + message mapping), `twilio/origination.py` (a thin Twilio REST client behind a port, so dialing is faked in tests), and three routes added to `app.py`: the TwiML endpoint, the WebSocket, and the status-callback endpoint.
2. **Dial.** After `POST /calls` records the call, the voice service originates a Twilio call whose TwiML is `<Connect><ConversationRelay>` with the fixed disclosure as `welcomeGreeting` (played in full), `dtmfDetection="true"`, and the minted `call_token` as a `<Parameter>`. The case id is never sent to Twilio or the model.
3. **Connect.** On the `setup` message the adapter verifies the `call_token` (bound to case + attempt + number) and that the dialed number is the pick read at dial time. Any failure closes the socket and fails the call closed (R7). The `X-Twilio-Signature` on the TwiML fetch and the status callback is verified; see Q46 for the WS handshake.
4. **Message mapping** (the live analog of `Line`): `prompt.voicePrompt` -> `CalleeSaid`; `dtmf.digit` -> `Keypad`; a `prompt` with `last` after `speechTimeout` with no words -> `Silence`; the guard-cleared agent text -> outgoing `text`; `press_keys` -> `sendDigits`. `step()` keeps the final say, as on the text line.
5. **Status callbacks.** A call that never connects maps to its result (`no_answer`, `busy`, `failed`), and answering-machine detection to `machine` with `answered_by: "machine"`. The callback is signature-verified, then reported to the core.
6. **Fail closed (R7).** A bad signature or token, a wrong dialed number, a WS error, or a mid-call disconnect ends the call `failed` and reports it; if the report cannot be delivered the case stays `calling` for the core's `call_timeout` (already handled by `report.py`).

## Acceptance

- [ ] The TwiML endpoint returns `<Connect><ConversationRelay>` carrying the disclosure as `welcomeGreeting` and the `call_token` as a `<Parameter>`; it never contains the case id, bank details, or the number's lineage.
- [ ] A `setup` with a valid token and the right dialed number proceeds; a missing, wrong, or replayed token, or a dialed-number mismatch, closes the socket and records no answer (R7).
- [ ] `prompt` and `dtmf` messages drive `step()` to the same outcomes the text line reaches for the same inputs (disclosure first, consent, order, last four), and only guard-cleared text is sent back.
- [ ] A signature-verified status callback for `no_answer` / `busy` / `failed` / `machine` reports that result to the core; a callback with a bad signature is rejected (401) and reports nothing.
- [ ] Failure path: a WS error or a mid-call disconnect ends the call `failed`, never a recorded "yes", and reports `failed` (or leaves the case `calling` for the core timeout if the report cannot land).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/voice/tests/test_twiml.py::test_connect_carries_disclosure_and_token` |
| 2 | `apps/voice/tests/test_relay.py::test_bad_token_or_wrong_number_fails_closed` |
| 3 | `apps/voice/tests/test_relay.py::test_ws_messages_reach_the_same_outcome_as_the_text_line` |
| 4 | `apps/voice/tests/test_status_callback.py::test_non_connect_results_are_reported_and_signed` |
| 5 | `apps/voice/tests/test_relay.py::test_disconnect_mid_call_fails_closed` |

All drive a fake in-memory WebSocket and `ScriptedAgent`; a fake Twilio REST client and a fake signature verifier stand in for the network. No live account, phone number, or model key.

## Out of scope

- **Next spec:** the live Claude agent behind the `Agent` port (its system prompt and the model pin; Q41 picks the model).
- The attack lab (its own spec).
- The Retell fallback (README "Setup and fallback"); separate if the go/no-go needs it.
- Pressing through a real phone menu (stretch).
- Provisioning the Twilio account and numbers, and the live go/no-go itself (ops; README).

## Open questions

- **Q45:** `welcomeGreeting` must play in full (R8), but `interruptible` is one attribute for the whole call. Does setting `interruptible="false"` only for the greeting need a separate turn, or does ConversationRelay always play the greeting fully? Confirm against Twilio docs before fixing the TwiML.
- **Q46:** Twilio signs the TwiML fetch and status callbacks with `X-Twilio-Signature`. Does it sign the WebSocket handshake too, or is the `call_token` the sole WS guard? If the latter, the token is the only thing standing between a stray socket and a call, so confirm it is enough.
- **Q47 (team):** the voice service originates the Twilio call via REST after `POST /calls` accepts (not the core). Confirm, and confirm the status-callback URL points back at the voice service.
- **Q48 (timing):** feature freeze is Nov 7 and code freeze Nov 8. This adapter's logic can land pre-freeze behind the text default (`DIALBACK_VOICE=text`); live calling depends on the Nov 4 go/no-go outcome. Is live calling still a Nov 10 goal, or does the text line plus the lab carry the demo?

## Commands

```bash
cd apps/voice && uv run pytest
python3 scripts/ci/boundaries.py
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
