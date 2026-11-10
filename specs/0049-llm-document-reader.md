# 0049: The LLM document reader (`DIALBACK_READER=llm`)

**Owner:** Inesh (drafted from Sami's session, for Inesh's review)  **Partner:** Abhi  **Slice:** evidence  **Target day:** not set

## Goal

Fill the slot spec 0022 left open: a quarantined model labels each phone number's role and party in
one document's visible text. Everything around the slot exists:

- custody;
- text and visibility, from the page model;
- candidates;
- grounding;
- storing.

Today `DIALBACK_READER=llm` raises "not built", and the keyword stub labels everything.

This serves:

- **R9:** document text is data. The model only labels numbers code found, and code parses every
  value from a quote it checks.
- **ADR-4:** models read documents; code decides.
- **The design's labeling row:** a quarantined model with no tools, returning schema-checked JSON,
  starting on Haiku 5.5, judged on a 100-document labeled set.
- **FINDINGS A2.** Today an honest mention such as "Call us at (415) 555-0142" is labeled
  `unknown`, which refuses the vendor's real number.

## Contract touched

None. Observations keep their shape. `extractor_version`, which is already stored, now says which
reader labeled a document.

## Decisions

1. **The shape.**
   - `LlmReader(model: LabelModel)` implements `Reader.label(visible_text)` and returns
     `[{quote, role, party}]`.
   - `reader_for(ReaderMode.LLM)` builds it with the real adapter.
   - The model client is injected, as the voice service's `ClaudeAgent` does, so every test runs on
     a fake.
2. **The call** (the real adapter).
   - It uses the official `anthropic` Python SDK and `client.messages.create` (Q107).
   - **The model** is pinned in one constant: `claude-haiku-5-5`.
   - **`output_config`:** `effort: "low"`, and `format` set to a JSON schema. Structured outputs are
     supported on Haiku 5.5, so the reply is always valid JSON in that shape.
   - **Other settings:** no tools, no sampling parameters (Haiku 5.5 rejects non-default ones),
     `max_tokens` 4096, a 30-second timeout, and the SDK's default retries.
   - **The schema:** `{"labels": [{"quote": string, "role": "main"|"ar"|"billing"|"fax"|"unknown",
     "party": "vendor"|"customer"|"bank"|"unknown"}]}`, with `additionalProperties: false`.
   - **The system prompt** is fixed in code. It defines each role and party, says the text is data
     and any instruction in it is to be ignored, and asks for the shortest quote that holds the
     number and its label. The document's visible text goes in the user turn, between fixed
     delimiters.
   - **What the model sees:** one document's visible text and nothing else. No custody, sender,
     subject, other documents or vendor name (spec 0022's rule; Q108).
3. **Fail closed.** For a single document, any of these gives no labels:
   - an API error;
   - a timeout;
   - a refusal (`stop_reason: "refusal"`; Haiku 5.5 has no server-side fallback);
   - a `max_tokens` stop;
   - output that fails the schema.

   With no labels, every candidate in the document stays `unknown`, which `select()` refuses. The
   failure is counted. The reader never falls back to the stub: a silent switch would change
   behavior without anyone choosing it.
4. **Configuration errors fail loudly.** `DIALBACK_READER=llm` with no API key in the environment
   refuses to start. `dialback-ledger demo load` reports how many documents failed, and exits
   non-zero if any did (Q111). Otherwise the demo could run with every number silently refused.
5. **Account numbers never leave.**
   - Before the call, any run of 7 or more digits that isn't a phone candidate is replaced with a
     placeholder, in the text sent to the model only (ADR-8's spirit).
   - Grounding still checks quotes against the original lines. So a quote that includes a
     placeholder fails grounding and is dropped, which fails closed. Asking for the shortest quote
     makes that rare (Q110).
6. **Nothing sensitive is logged.** The reader never logs document text or model output. It logs
   counts and the API's request id only.
7. **The configuration is recorded.**
   - `extractor_version` becomes the reader mode plus a short hash of the model id, the prompt and
     the schema. `demo.py`'s `EXTRACTOR` is the stub's value today.
   - So the lineage panel and the lab can tell which reader labeled a sighting.

## The evaluation (fixed before the reader's first scored run)

**The labeled set.** 100 documents, with the role and party of every visible phone number checked
by a person (Q109), never labeled by a model.
- **Sources:** the 20-scenario corpus (its truth is known by construction) and the demo seed
  documents.
- **New hand-written documents,** at least 40, covering what the stub can't place:
  - "call us at" and other mentions without a label;
  - a dash instead of a colon;
  - the label on the line above the number;
  - signatures and tables;
  - two numbers on one line;
  - a bank's or the customer's number labeled only by its context.
- The set is frozen with a hash before the first scored run.
- It is split into 30 dev documents, to write the prompt against, and 70 held-out documents, scored
  only for the result.

**What is counted, for each labeled number:**
- **Unsafe label** (the security metric): a number that isn't the vendor's (a bank's, the
  customer's, or one an attacker planted) labeled party `vendor`.
- **Over-block** (the accuracy metric): a vendor number left without party `vendor` and a role.
- **Role accuracy** among vendor numbers, reported.

**The bar,** on the held-out 70. The model's output varies, so it runs 3 times, and the bar must
hold on every run:
- unsafe labels are no more than the stub's on the same documents, and every one is listed;
- over-blocks are fewer than the stub's, by a one-sided exact McNemar test, p <= 0.05, on the paired
  per-number outcomes.

**If Haiku 5.5 fails.** The design's next step is Sonnet 5.5. It is tuned on the dev set only, gets
one held-out run of 3 repeats, and the report says two models were tried. If neither passes, `stub`
stays the default and the result is written down here.

## Acceptance

- [ ] `DIALBACK_READER=llm` gives an `LlmReader`, and the default stays `stub`.
- [ ] With a fake model, each failure in decision 3 gives no labels and is counted, and the reader
  never calls the stub.
- [ ] A missing API key stops `llm` mode at start. `demo load` exits non-zero when any document
  failed.
- [ ] The text sent to the model has no run of 7 or more digits that isn't a phone candidate. A
  quote with a placeholder is dropped by grounding.
- [ ] No document text or model output reaches a log line, under any failure.
- [ ] `extractor_version` names the reader and changes when the model, prompt or schema changes.
- [ ] **A worst-case reader.** Label every visible number the vendor's main line, and every corpus
  number keeps every reason except `not_vendor_party` and `fax_line`. So no label can make a number
  callable on its own: R2, the channel count, age, near misses and hidden text all still hold.
- [ ] A hostile model's output (invented numbers, quotes from hidden text, extra fields) is dropped,
  as spec 0022 already requires for the stub.
- [ ] The labeled set is frozen with its hash before the first scored run. The evaluation report
  gives all three counts for each run, for both readers, and the bar's result.

## Tests (written first)

| Item | Test |
|---|---|
| Mode and wiring | `packages/ledger/tests/test_llm_reader.py::test_llm_mode_builds_the_reader`, `::test_default_stays_stub` |
| Fail closed | `test_llm_reader.py::test_each_failure_gives_no_labels` (error, timeout, refusal, `max_tokens`, schema), `::test_never_falls_back_to_the_stub` |
| Configuration | `test_llm_reader.py::test_missing_key_refuses_to_start`, `packages/ledger/tests/test_demo.py::test_failed_documents_fail_the_load` |
| Redaction | `test_llm_reader.py::test_long_digit_runs_never_reach_the_model`, `::test_a_quote_with_a_placeholder_is_dropped` |
| Logs | `test_llm_reader.py::test_no_text_in_logs` |
| Version | `test_llm_reader.py::test_extractor_version_tracks_model_prompt_and_schema` |
| Worst case | `packages/ledger/tests/test_select_properties.py::test_labels_only_change_party_and_fax_reasons` |
| Hostile output | `packages/ledger/tests/test_grounding.py` (extended to the LLM reader's output shape) |
| Evaluation | `packages/ledger/scripts/reader_eval.py` (live, on demand, never in CI), with `test_reader_eval.py` checking its counting and the McNemar test on fixed inputs |

## Out of scope

- Reading real PDFs, emails or scans (vision), and getting documents in (an inbox or uploads).
- Giving the model the vendor's name (Q108).
- Bank and routing-number candidates (spec 0022, Q44).
- The lab's red team of this reader (FINDINGS D). It needs this reader to exist first.

## Open questions (for Inesh)

- **Q107.** The official `anthropic` SDK (my call: typed errors, retries, structured outputs) or
  raw `httpx`, as the voice service does.
- **Q108.** Visible text only (spec 0022's rule), or also the vendor's legal name. The name would
  let the model tell the vendor's number from the customer's, but it widens what the quarantined
  model sees.
- **Q109.** Who checks the 100 documents' labels by hand: a person must, and it is the real cost of
  this spec.
- **Q110.** Replacing long digit runs before the call (my call). The cost is that a quote including
  one is dropped.
- **Q111.** Whether one failed document should fail `demo load` (my call), or only be reported.

## Commands

```bash
cd packages/ledger && uv run pytest -q
uv run --no-project --with ruff ruff check packages/ledger
DIALBACK_READER=llm uv run python packages/ledger/scripts/reader_eval.py --split dev
```
