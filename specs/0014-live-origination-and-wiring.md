# 0014: Live calling, part 1: origination, the call lookup, and wiring

**Owner:** Sami  **Slice:** call and adversary  **Target day:** Fri Nov 7 (logic pre-freeze; a real dial is still gated by the go/no-go and the Twilio account)

## Goal

A placed call actually rings a phone. Spec 0013 built the ConversationRelay routes and left three seams that only a live account or a model can fill: the REST call that dials, the per-call `lookup_call` that the routes read, and the wiring that builds a real `TwilioDeps`. This spec fills the first two and the wiring, all testable with fakes, and leaves the one seam that needs a model, `agent_for`, for the spec after the attack lab. When this is done, `DIALBACK_VOICE=live` with a provisioned account dials the Trust Ledger's pick over Twilio, runs the call the routes already drive, and reports the result, with no case id or bank detail ever sent to Twilio (R2, R3, R4). It serves R7 by failing closed at every new edge: a half-configured live mode, a missing pick, or a dial that Twilio rejects.

## Contract touched

None. The report already exists (`internal.yaml` `reportCallEnded`, spec 0012). The Twilio REST call and its webhooks are Twilio's shapes, not ours. `voice.calls` already carries `provider`, `provider_call_id` (the CallSid) and `status`, so recording the dial needs no migration.

## Decisions (proposed; Sami to confirm)

1. **New code, all in `apps/voice`.** `twilio/origination.py` gains an `Originator` port (one `originate(...)` method) and a `TwilioOriginator` that wraps the Twilio REST client; the tests pass a fake. `store.py` gains the DB-backed `lookup_call(call_id) -> CallDial | None` beside the existing `CallStore`. A new `wire.py` builds a `TwilioDeps` from `Settings` and the real seams. `settings.py` is extended to read the live config. `agent.py`, `transport.py`, `relay.py` and the routes in `app.py` are unchanged except for the one call site that dials.

2. **Dial synchronously, in the `POST /calls` path (Q47).** On a fresh placement (the handler recorded a new call, `created=True`), after `record_call` and before returning 202, the handler asks the `Originator` to dial: `to` is the pick recorded on the call, `from_` is the configured Twilio number, `url` is `{public_base_url}/twilio/twiml/{call_id}`, and `statusCallback` is `{public_base_url}/twilio/status/{call_id}`. The returned CallSid is written to `voice.calls.provider_call_id` with `provider='twilio'` and `status='ringing'`. The case id and the dialed number's lineage are never passed to Twilio. One Twilio call per placement: a replayed Idempotency-Key returns the first call (`created=False`) and does not dial again.

3. **An origination failure fails closed (R7).** If the `Originator` raises or Twilio rejects the request, no live call exists. The handler marks the call `result='failed'`, `reason='originate_failed'`, reports `failed` to the core through the existing `CoreClient`, and still returns 202 with the `call_id`: the dial outcome rides the normal report path, exactly as a call that rings and then drops does, so `POST /calls`'s contract (202/200/409/401/422) is unchanged. If the failed report cannot land, the case stays `calling` for the core's `call_timeout` (already handled by `report.py`). Rejected alternative: returning 5xx so the core retries, which couples the core's retry loop to Twilio's REST availability and risks double-dialing.

4. **`lookup_call` is a fail-closed DB adapter.** It reads the call row from `voice.calls` by id and assembles the `CallDial` the routes need: `case_id` and `attempt` from the row, `number_e164` from the pick (`contact_point_id`), and `customer_name`, `callback_number` and `vendor_names` from the case and the pick's vendor. If the row is missing, the pick is missing, or any required field cannot be read, it returns `None`; the routes already treat `None` as fail-closed (the TwiML fetch is 404, the WebSocket closes, the status callback reports nothing). It never invents a number (R2).

5. **The wiring fails closed on half-configuration.** `voice_mode` stays the pure requested flag (`text` or `live`), because an existing test pins `load_settings({"DIALBACK_VOICE":"live"}).voice_mode` to `live`. The "fall back to text" is expressed one level up: `load_settings` assembles a validated live config (the Twilio Account SID and Auth Token, the Twilio `from` number, the `call_token` key, the core base URL and internal token, and the public and ws base URLs) **only when the mode is `live` and every required value is present**, and leaves that config absent otherwise, logging the missing keys by name. There is no half-live config. `wire.py` builds `TwilioDeps` only when that validated config is present, with `originator=TwilioOriginator(...)`, `lookup_call` the DB adapter, `report_ended=CoreClient(...).report_call_ended`, `clock` the wall clock, and `agent_for=lambda: ScriptedAgent()` as the placeholder the next spec replaces with the live model. A live-but-incomplete deployment therefore gets `create_app(twilio=None)`, mounts no Twilio route, and runs the text line, exactly as today (R7).

## Acceptance

- [ ] In live mode, a fresh `POST /calls` placement originates one Twilio call whose `to` is the recorded pick, whose `from_` is the configured number, and whose `url` and `statusCallback` are built from `public_base_url`; the returned CallSid is stored on `voice.calls` and neither the case id nor the number's lineage is passed to Twilio.
- [ ] A replayed Idempotency-Key returns the first call (200) and places no second Twilio call.
- [ ] The `lookup_call` mapper (`dial_from_row`) returns a `CallDial` for a complete call row and `None` when the row or any token-or-disclosure field is missing, so the routes fail closed (404 / socket closed / nothing reported). The psycopg query that builds the row joins `voice.calls`, `app.cases` and the ledger pick, and lands with the ledger (Q40/Q50).
- [ ] In live mode with any required secret or URL missing, the service starts in text mode and mounts no Twilio routes, logging the missing keys; it never starts half-live.
- [ ] Failure path: an `Originator` error ends the call `failed` and reports `failed` to the core (or leaves the case `calling` for the core timeout if the report cannot land); it is never a placed-but-silent live call, and never a recorded "yes".

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/voice/tests/test_origination.py::test_live_placement_dials_the_pick` |
| 2 | `apps/voice/tests/test_origination.py::test_a_replayed_key_places_no_second_call` |
| 3 | `apps/voice/tests/test_lookup_call.py` (the `dial_from_row` mapper and its fail-closed contract; the psql query that fills a row lands with the ledger, Q40/Q50) |
| 4 | `apps/voice/tests/test_wiring.py::test_live_without_config_falls_back_to_text` |
| 5 | `apps/voice/tests/test_origination.py::test_originate_failure_reports_failed` |

All drive a fake `Originator` that records its arguments, an in-memory store, and signed `TestClient` requests, plus an httpx `MockTransport` for the `TwilioOriginator` REST client. The `lookup_call` mapper (`dial_from_row`) is tested pure; its psycopg query is the deferred thin piece the psql CI test will cover once the ledger lands, exactly as the `POST /calls` store adapter is. No live Twilio account, phone number, or model key.

## Out of scope

- **Next spec (after the attack lab):** the live Claude agent behind `agent_for` (its system prompt and the model pin; Q41). Until then the wiring injects `ScriptedAgent`, so a live dial would run the scripted brain, which is why a real model dial waits on the lab's `lab-smoke` gate.
- The attack lab (its own spec).
- Provisioning the Twilio account and number, the public URL, and the account secrets (ops; README "Setup and fallback").
- The Retell fallback.
- The live go/no-go itself (Q48).

## Open questions

- **Q49:** origination is synchronous in the `POST /calls` path (decision 2). If Twilio REST latency makes the 202 too slow, it moves to a background task kicked off after the 202, which needs a "dialed yet?" guard so a process that dies between `record_call` and the dial still fails closed. Confirm synchronous is fine for the demo.
- **Q50:** `lookup_call` needs `customer_name`, `callback_number` and `vendor_names` (decision 4). These cross into `app.cases` (Abhi) and the Trust Ledger (Inesh), whose shapes are not final (spec 0012, Q40). Confirm each field's source. In particular, `callback_number` is likely a single configured team number, not a per-case column; confirm. Until confirmed, the adapter reads what it can and fails closed on the rest.
- **Q51:** an origination failure records `failed`, reports `failed`, and still returns 202 (decision 3). Confirm this over returning a 5xx that makes the core retry. **Found while building:** because the dial is synchronous (Q49), the failed report is awaited inside `POST /calls`, before the 202. In production the core has not appended `call_started` yet at that point, so the report hits the 409 race that `report.py` retries with backoff, and can burn much of that backoff inside the request. It still fails closed (the core's `call_timeout` is the backstop), but it reinforces Q49's background-task alternative, which fixes both the latency and this race. Until then the failed-report await is accepted as a rare, slow path.
- Carried from 0013: **Q45** (whether the greeting alone can be non-interruptible), **Q46** (the WS handshake is not Twilio-signed and the `call_token` is a stateless HMAC, not single-use; decide if a single-use nonce is needed before a real dial), **Q47** (confirmed here: the voice service originates via REST and the status callback points back at it), **Q48** (the live go/no-go), **Q41** (the model pin, in the agent spec).

## Commands

```bash
cd apps/voice && uv run pytest
python3 scripts/ci/boundaries.py
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
