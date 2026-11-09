# 0034: The live-call blockers

**Owner:** Sami  **Slice:** call and adversary  **Target day:** Nov 8 (before the video)

## Goal

Today no live call can complete a verification. Five independent defects, found by the Nov 8 code
review and a build of the deploy image, stop it before or during the call; each one fails closed, so
money was never at risk, but the live demo cannot work. When this is done the live path can connect
the ConversationRelay socket, hear and record every answer the decision engine needs, and reach
`verified` on a clean call, while every failure still fails closed (R7). It serves R3 (code decides
from the recorded answers), R7 and R8.

| # | Blocker | Where | Effect today |
|---|---|---|---|
| B1 | The model request sends `temperature: 0.0`, which `claude-sonnet-5-5` rejects with a 400; `max_tokens: 16` also leaves no room once thinking runs by default | `claude_agent.py` | every consent is classified `no` (fail closed), the call ends "we won't continue", and the recorded `no` raises NR19 on later calls |
| B2 | `create_asgi_app` defaults `clock=time.monotonic`, a float, where a `datetime` is required | `entrypoint.py` | every live call ends `failed: internal_error:AttributeError` at the first answer |
| B3 | Twilio signature check drops blank form fields before verifying, and leaves the query string out of the signed URL | `app.py`, `origination.py` | a webhook with any empty field (`CalledCity=`) gets a 401 |
| B4 | The image installs plain `uvicorn`, with no WebSocket backend | `apps/voice/pyproject.toml` | uvicorn answers the ConversationRelay upgrade with a 404 |
| B5 | The name-and-role question records only `speaker_name`; V06 needs a `speaker_role` | `script.py`, both agents | no call can ever reach `verified` |

## Decisions

- **D1. Thinking off, low effort (B1).** The request sends no sampling parameters, `thinking: {"type":
  "between_tools"}` (the lowest thinking setting on Sonnet 5.5) and `output_config: {"effort": "low"}`,
  with `max_tokens: 256`. A live turn is dead air while the model runs, and a three-way
  classification gains nothing from thinking, so predictable latency wins over adaptive thinking.
  `between_tools` is accepted only on Sonnet 5.5, so a change of `MODEL_PIN` must revisit it. Cost if
  wrong: slightly worse classification on hard turns; the fix is adaptive thinking at low effort,
  measured on live traffic.
- **D2. Name and role are two questions (B5).** Decided by Sami on Nov 8 over two alternatives
  (taking the words after a comma as the role, or a model yes/no on "did they state a role"). The
  script asks "May I have your name?" then "And what's your role there?", and each answer is recorded
  as said, so neither agent parses one answer into two slots. Cost: one more question per call.
- **D3. Twilio's published signature scheme, tested against Twilio's own vectors (B3).** The signed
  string is the full URL Twilio requested, query string included, then every form field, blank
  values included, as key and value, sorted by key then value, duplicates removed. The tests use the
  vectors from twilio-python's `tests/unit/test_request_validator.py`, not a re-implementation, so a
  divergence from Twilio fails a test.
- **D4. `uvicorn[standard]` (B4).** The extra brings `websockets`, the backend uvicorn picks for
  `--ws auto`, and the rest of uvicorn's recommended production set.
- **D5. A punctuated one-word answer is read (B1, added after the branch review).** With the request
  fixed, the next live failure would be a model that answers `Yes.`: that is not in the allowed set,
  so consent would fail closed to `no` and end the call. The answer is lowered and stripped of
  surrounding spaces, punctuation and quotes before the allowed-set check, which still rejects
  anything that is not exactly one allowed word. Cost if wrong: none found, since a sentence still
  fails closed.

## Contract touched

None. `speaker_role` is already a slot in `call-answers.schema.json`, and no contract names a call
phase. The phase rename (`name_role` becomes `name` and `role`) is internal to `packages/policy`.

## Acceptance

- [ ] **B1.** The Anthropic request carries no `temperature`, `top_p` or `top_k`; it sends
  `thinking: {"type": "between_tools"}`, `output_config: {"effort": "low"}` and `max_tokens: 256`. A
  response whose first block is a `thinking` block still yields the text answer. A punctuated
  one-word answer (`Yes.`) is read as `yes`, and anything outside the allowed set still fails closed
  (D5). An API error, or a response with no text block, still records the phase's safe value.
- [ ] **B2.** `create_asgi_app`'s default clock returns a timezone-aware UTC `datetime`, and a relay
  call on the default clock records the callee's consent instead of ending `internal_error`.
- [ ] **B3.** Twilio's published vectors validate (a single-valued POST to a URL with a query string,
  and a duplicate-key POST). A correctly signed TwiML fetch and status callback that carry a blank
  field are accepted. The signed URL includes the query string. A wrong or missing signature is
  still rejected with 401.
- [ ] **B4.** `dialback-voice` declares `uvicorn[standard]`, and uvicorn in the voice environment
  loads a WebSocket protocol for `ws="auto"`. A build of `apps/voice/Dockerfile` answers a WebSocket
  upgrade with `101 Switching Protocols` (checked by hand; the output goes in the PR).
- [ ] **B5.** The script asks, in order: consent, company, department, name, role, change, last four.
  Both agents record the name answer as `speaker_name` and the role answer as `speaker_role`, each
  citing its own turn and grounded. A recorded `speaker_changed` flag re-asks both name and role. A
  clean scripted call records a grounded `speaker_role`, and `decide()` on that call returns
  `verified` with V06 met.
- [ ] **Failure paths stay closed.** A model error still yields consent `no`, change `unsure`, last
  four `dont_know`; a bad Twilio signature still gets 401; a call that is silent at the role question
  follows the existing silence rule.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| B1 | `apps/voice/tests/test_claude_agent.py::test_the_request_sends_no_sampling_params_and_turns_thinking_off` |
| B1 | `apps/voice/tests/test_claude_agent.py::test_a_thinking_block_before_the_text_still_yields_the_answer` |
| B1, D5 | `apps/voice/tests/test_claude_agent.py::test_a_punctuated_one_word_answer_is_still_read`, `::test_an_answer_that_is_more_than_one_word_still_fails_closed` |
| B2 | `apps/voice/tests/test_entrypoint.py::test_the_default_clock_is_the_utc_wall_clock` |
| B3 | `apps/voice/tests/test_twilio_signature.py` (Twilio's vectors, blank fields, query string, rejections) |
| B4 | `apps/voice/tests/test_entrypoint.py::test_uvicorn_has_a_websocket_backend` |
| B5 | `packages/policy/tests/test_script.py` (order and slots), `packages/policy/tests/test_step.py::test_a_new_speaker_is_asked_name_and_role_again` |
| B5 | `apps/voice/tests/test_transport.py::test_a_clean_call_records_name_and_role_separately` |
| B5 | `apps/voice/tests/test_claude_agent.py` (name and role as free-text phases) |

## Out of scope

The other findings of the Nov 8 review, each its own change: consent and change negations, account
digits inside free-text answers, the last-four parse, answers grounded to the wrong question after a
silence, the pressure flag and consent order, opt-out never recorded, a dropped call's answers, socket
errors that skip the report, customer names with digits, voicemail detection on the live path, the
synchronous model turn (Q55), and the voice SQL findings. Also out of scope: adapting the attack-lab
branches (`sami/attack-lab-voice`, `sami/attack-lab-generative`) to the two-question script, which
lets their `LabAgent` role workaround be removed; that happens when those branches rebase.

## Open questions

- **Q65. Refusal fallback.** The skill default for Sonnet 5.5 is server-side `fallbacks`; this spec
  does not enable it, because a silent switch to another model would bypass the lab-gated model pin
  (Q41). A refusal today returns no text, so the turn fails closed. Revisit together with the pin.
- **Q66. Free-text answers record any words.** A role answer of "I'd rather not say" is recorded as a
  role and satisfies V06, exactly as a name answer does today. This pairs with the review finding
  about whole utterances (and digits) in free-text slots.
- **Q68. A speaker change on the same turn as an answer.** The re-ask rule counts an answer at or
  after the `speaker_changed` flag's turn. If one turn carries both the old speaker's role and the
  flag ("I'm the AR clerk, let me get my manager"), the name is asked again but the role is not, which
  misses B5's "re-asks both" in that one case. It cannot release anything: NR17 sends any call with a
  `speaker_changed` flag to review. Found by the branch review; left as is because a same-turn answer
  can belong to either speaker.
- **Q67. GET TwiML fetches.** We never ask Twilio for GET, so only POST is live; the signing now
  matches Twilio's scheme for both.

## Commands

```bash
cd packages/policy && uv run pytest -q
cd apps/voice && uv run pytest -q
DATABASE_URL=postgresql://... uv run --directory apps/voice pytest -q   # the Postgres-backed tests too
uv run --no-project --with ruff ruff check apps packages scripts corpus
python3 scripts/ci/boundaries.py
scripts/db/test.sh
docker build -f apps/voice/Dockerfile -t dialback-voice:0034 .   # then a WebSocket upgrade probe: 101
```
