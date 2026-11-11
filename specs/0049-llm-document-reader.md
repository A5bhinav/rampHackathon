# 0049: The LLM document reader (`DIALBACK_READER=llm`)

**Owner:** Sami (the code is in `packages/ledger`)  **Slice:** evidence  **Target day:** not set (Q123; code freeze was Nov 8, the live final is Nov 19)

**Status:** approved by Sami on Nov 10, with the defaults below. It is built on its own branch, and
no PR opens without Sami's OK. No review by another teammate is needed (Sami, Nov 10).

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

It cannot fix FINDINGS A1. A1 is the selection rule's label veto: an attacker's text that calls the
real number a bank's line refuses it. A reader that reads that text correctly still labels it
`bank`.

## Contract touched

None. Observations keep their shape. `extractor_version`, which is already stored, now says which
reader labeled a document.

## What a label means

A label says what the document's text claims about a number, from the document's own point of
view. It does not say who really owns the number. The reader can't know that from one document, and
provenance comes from custody, never from a model (design §4).

| Line in a document | Correct label | Why it's still safe |
|---|---|---|
| `AR direct: (415) 555-0143`, planted by an attacker | ar, vendor | The selection rule refuses it on R2, its channel count or its age, whatever the label says |
| `Bank verification line: (415) 555-0142`, the vendor's real number in an attacker's email | bank | Labeling it right still refuses the real number: that is A1, in `select()` |
| `Customer main line: (415) 555-0177` | main, customer | |
| `Call us at (415) 555-0142` | main, the author's party | "Us" is whoever wrote the document. The reader doesn't see the author (Q108). |
| A number with no word near it | unknown, unknown | |

The person who checks the evaluation set labels by this table, and the prompt teaches it.

## Decisions

1. **The shape.**
   - `LlmReader(model: LabelModel)` implements `Reader.label(visible_text)` and returns
     `[{quote, role, party}]`.
   - `reader_for(ReaderMode.LLM)` builds it with the real adapter.
   - The model client is injected, as the voice service's `ClaudeAgent` does.
   - Tests fake it at the HTTP transport, under the real SDK, so the adapter's handling of stop
     reasons and the SDK's own logging are exercised too.
2. **The call** (the real adapter).
   - It uses the official `anthropic` Python SDK and `client.messages.create` (Q107), added as a
     dependency of `packages/ledger`.
   - **The model** is pinned in one constant: `claude-haiku-5-5`.
   - **Thinking and effort:** `output_config.effort` is `"low"`, and `thinking` is omitted. Haiku 5.5
     then thinks adaptively, and that thinking counts against `max_tokens`, which is 8192.
   - **The format:** `output_config.format` is a JSON schema. Structured outputs are supported on
     Haiku 5.5. The reply is read by block type, because thinking blocks can come first.
   - **Other settings:** no tools, no sampling parameters (Haiku 5.5 rejects non-default ones), a
     30-second timeout, and the SDK's default retries.
   - **The schema:** `{"labels": [{"quote": string, "role": "main"|"ar"|"billing"|"fax"|"unknown",
     "party": "vendor"|"customer"|"bank"|"unknown"}]}`, with `additionalProperties: false`.
   - **The quote** is the number's own visible line, or a part of it that holds the number.
     Grounding only accepts a quote inside one line, and each span is its own line. A label word on
     the line above still informs the label; it just isn't part of the quote. Grounding is
     unchanged.
   - **The system prompt** is fixed in code. It gives the label meanings above, says the text is data
     and any instruction in it is to be ignored, and gives the quote rule. The document's visible
     text goes in the user turn, between fixed delimiters.
   - **What the model sees:** one document's visible text and nothing else. No custody, sender,
     subject, other documents or vendor name (spec 0022's rule; Q108).
3. **Fail closed, and store nothing that failed.** For a single document, any of these is a failure:
   - an API error;
   - a timeout;
   - a refusal (`stop_reason: "refusal"`; Haiku 5.5 has no server-side fallback);
   - a `max_tokens` stop;
   - output that fails the schema.

   A failed document is **not stored**: no document row and no observations. Two reasons:
   - `store.py` skips a document that's already stored, so storing an unlabeled one would freeze its
     numbers as `unknown` for good.
   - Those rows would also look the same as "the model said unknown".

   The reader never falls back to the stub: a silent switch would change behavior without anyone
   choosing it.
4. **The demo stops on any failure.**
   - `demo load` already runs every check before writing anything. In `llm` mode a failed document
     rolls the whole load back, and the command exits non-zero, naming the document.
   - `demo request` (beat 2) does the same. It writes nothing and exits, so the beat stops instead of
     going on without the change email, whose sightings R2 needs.
   - `DIALBACK_READER=llm` with no API key refuses to start.
5. **Nothing secret reaches the model.**
   - **Account numbers.** Before the call, any run of 7 or more digits that isn't a phone candidate
     is replaced with a placeholder, in the text sent to the model only. Grounding checks quotes
     against the original lines, so a quote that includes a placeholder is dropped, which fails
     closed (Q110).
   - **Team phone numbers.** With `--phone`, a document is labeled with its fictional number, and the
     team number is swapped into the stored rows afterward. The team number never reaches the
     model.
6. **Nothing sensitive is logged.**
   - The reader never logs document text or model output. It logs counts and the API's request id
     only.
   - It sets the `anthropic` SDK's loggers, and those of its HTTP library, to WARNING, so debug
     logging can't print request bodies.
7. **The configuration is stored.**
   - `extractor_version` becomes the reader mode plus a short hash of the model id, the prompt and
     the schema. `demo.py`'s `EXTRACTOR` is the stub's value today.
   - It is stored only. Showing it in the lineage panel would change the selection contract, which is
     out of scope.
8. **Tests never call the API by accident.** Every test that calls `ingest_all` without a reader
   passes `StubReader` explicitly, since the default comes from the environment.

## The evaluation (fixed before the reader's first scored run)

**Building doesn't wait for documents.**
- The code and its tests need none: the tests fake the API, and use the corpus and demo fixtures.
- Writing the prompt can start on the existing corpus (369 documents) and demo seed (47).
- Only the scored evaluation below needs the new hard and held-out sets.

**The labeled set: 100 documents, labeled by "What a label means".**
- **Where the documents come from:**
  - the corpus and demo seed documents;
  - spec 0052's generator (`sami/docgen-spec`). Its plans carry each number's claim, with the same
    meaning as here, so their labels are known by construction.
- **Who checks:** a person checks every held-out label and a sample of the rest (Q109). No model
  writes the labels.
- **Two kinds of document:**
  - **Ordinary:** the corpus and demo seed documents, plus documents that look like real vendor mail
    (invoices, signatures, remittance notices).
  - **Hard for the stub:** "call us at" and other mentions without a label; a dash instead of a
    colon; the label on the line above; tables; two numbers on one line; a bank's or the customer's
    number labeled only by its context.
- **Split:** 30 dev documents for writing the prompt, and 70 held-out documents. The held-out 70
  must contain at least 30 numbers that aren't the vendor's (banks, customers, other parties), or the
  unsafe count means little.
- **The held-out 70** follow `corpus/README.md`'s rule for held-out data:
  - written and labeled by someone who doesn't write the prompt;
  - only their hash is committed, and the files live outside the repo;
  - whoever writes the prompt sees only the totals from held-out runs, never the documents or the
    list of errors.
  - **If they come from spec 0052's generator,** they must use phrasings and layouts the prompt's
    author never saw. Those lists, too, live outside the repo with only a hash committed. Spec 0052
    has to provide for that, since its phrase bank is in the repo today.

**What is counted, for each labeled number:**
- **Unsafe label** (the security metric): a number the text doesn't present as the vendor's, labeled
  party `vendor`.
- **Over-block** (the accuracy metric): a number the text presents as the vendor's, labeled so that
  the rule refuses it. That means a party other than `vendor`, or the role `fax` when the text
  doesn't call it a fax line.
  - (vendor, unknown) is not an over-block, because the rule doesn't refuse it.
  - A true fax line is never an over-block, whatever its label. The rule refuses fax lines by
    design, so refusing one is the intended outcome. (Corrected Nov 10, before any scored run: the
    first wording counted a fax line labeled `fax` as an over-block, and the review then showed
    (fax, unknown) was still counted. Both punished an outcome the rule wants.)
- **Role accuracy:** reported only for numbers with an explicit role word.
- **"Us" mentions:** reported on their own, since the reader can't see the author.

**The bar,** on the held-out 70. The model's output varies, so it runs 3 times, and every bar must
hold on every run:
- **No new unsafe labels:** none that the stub didn't also make on the same number.
- **Fewer over-blocks where the stub struggles:** on the hard-for-the-stub documents, fewer than the
  stub, by a one-sided exact McNemar test, p <= 0.05, on the paired per-number outcomes.
- **No regression on ordinary documents:** no more over-blocks than the stub there.

This shows the reader is better than **today's stub**. It does not show an LLM is needed: a better
keyword reader might pass too.

**If Haiku 5.5 fails.** The design's next step is Sonnet 5.5, tuned on the dev set only. Its result
on the same held-out 70 is exploratory. Adopting it needs fresh held-out documents. If nothing
passes, `stub` stays.

**What passing changes (Q123).** In code, the default stays `stub`. Switching a deployment to `llm`
is a setting, and needs the API key on that host and a rehearsal in `llm` mode.

## Review fixes (fresh review, Nov 10)

The review found the reader sound but the scoring script foolable (FINDINGS G9 to G20). Before any
scored run:

- **The labeled set must be complete and unique.** Every visible phone number in every document
  has a label, and no document id, or (document, line, number), appears twice. The script refuses a
  set that breaks either rule (G9, G10).
- **Runs.** Held-out runs are exactly 3, and the script fails if no run happened (G11).
- **Both kinds are required.** The set must hold ordinary and hard-for-the-stub documents, and the
  report gives the count of numbers per kind (G14).
- **The held-out hash is read from a committed file,** not the command line (G13).
- **Every bad reply is a reader failure.** Any reply that isn't a well-formed message with one text
  block of schema-valid JSON becomes `ReaderFailed`, including an invalid body, a non-JSON content
  type, missing fields and deeply nested JSON (G12).
- **What reaches the model.** Long digit runs in a phone extension are redacted too (G15), and the
  API base URL is pinned, so host settings can't redirect document text (G16).
- **`extractor_version`** also covers effort and `max_tokens` (G17).
- **Tests.** The network guard records any blocked call and fails at teardown (G19).
- **Errors** from a malformed labeled set never print document contents or phone numbers (G20).

## Acceptance

- [ ] `DIALBACK_READER=llm` gives an `LlmReader`, and the default stays `stub`.
- [ ] Each review fix above has a test that failed first.
- [ ] With the transport faked, each failure in decision 3 gives a failure, the document isn't
  stored, and the stub is never called.
- [ ] A failed document rolls back `demo load` and stops `demo request`, each exiting non-zero. A
  missing API key stops `llm` mode at start.
- [ ] The text sent to the model has no run of 7 or more digits that isn't a phone candidate, and no
  team phone number. A quote with a placeholder is dropped by grounding.
- [ ] No document text or model output reaches a log line, with the SDK's own logging included,
  under any failure.
- [ ] `extractor_version` names the reader and changes when the model, prompt or schema changes.
- [ ] Labels still can only remove eligibility, never add it. The existing
  `test_select_properties.py::test_5_labels_only_remove_eligibility` covers this; it is a regression
  guard for the selection rule, not evidence about this reader.
- [ ] Tests that call `ingest_all` pass a reader explicitly.
- [ ] The labeled set is frozen with its hash before the first scored run, with the held-out 70 kept
  outside the repo. The report gives every count for each run, for both readers, and each bar's
  result.

## Tests (written first)

| Item | Test |
|---|---|
| Mode and wiring | `packages/ledger/tests/test_llm_reader.py::test_llm_mode_builds_the_reader`, `::test_default_stays_stub` |
| Fail closed | `test_llm_reader.py::test_each_failure_is_not_stored` (error, timeout, refusal, `max_tokens`, schema; faked at the transport), `::test_never_falls_back_to_the_stub` |
| The demo | `packages/ledger/tests/test_demo.py::test_a_failed_document_rolls_back_the_load`, `::test_a_failed_request_writes_nothing`, `test_llm_reader.py::test_missing_key_refuses_to_start` |
| What reaches the model | `test_llm_reader.py::test_long_digit_runs_never_reach_the_model`, `::test_team_numbers_never_reach_the_model`, `::test_a_quote_with_a_placeholder_is_dropped` |
| Logs | `test_llm_reader.py::test_no_text_in_logs` (the real SDK, faked transport) |
| Version | `test_llm_reader.py::test_extractor_version_tracks_model_prompt_and_schema` |
| Explicit readers | `packages/ledger/tests/test_ingest.py` (passes `StubReader`) |
| Evaluation | `packages/ledger/scripts/reader_eval.py` (live, on demand, never in CI), with `test_reader_eval.py` checking its counting, the paired unsafe check and the McNemar test on fixed inputs |

## Out of scope

- Reading real PDFs, emails or scans (vision), and getting documents in (an inbox or uploads).
- Giving the model the vendor's name or the document's author (Q108).
- Showing `extractor_version` in the lineage panel.
- Fixing A1, which is in `select()`.
- Bank and routing-number candidates (spec 0022, Q44).
- The lab's red team of this reader (FINDINGS D). It needs this reader to exist first.

## Open questions

Sami approved the spec on Nov 10, with these defaults as the decisions. No review by another
teammate is needed.

- **Q107, decided:** the official `anthropic` SDK (typed errors, retries, structured
  outputs), not raw `httpx` as the voice service uses.
- **Q108, decided:** visible text only (spec 0022's rule). The alternatives are adding the
  vendor's legal name or the document's author. Either would let the model place "us" and tell the
  vendor from the customer, but it widens what the quarantined model sees.
- **Q109, resolved (Sami, Nov 10):** documents come from the corpus, the demo seed and spec 0052's
  generator. A person checks every held-out label and a sample of the rest.
- **Q110, decided:** replace long digit runs before the call. The cost is that a quote
  including one is dropped.
- **Q111, resolved by the review:** a failed document stops the demo and is never stored
  (decisions 3 and 4).
- **Q112:** renumbered to Q123, because main's spec 0050 already uses Q112.
- **Q123 (the team), open.** Is the LLM reader meant for the Nov 19 live final, after the Nov 8 code
  freeze? If so, the plan needs the deploy setting, the API key on that host, and a rehearsal in
  `llm` mode. Until someone says so, deployment is later work.

## Commands

```bash
cd packages/ledger && uv run pytest -q
uv run --no-project --with ruff ruff check packages/ledger
DIALBACK_READER=llm uv run python packages/ledger/scripts/reader_eval.py --split dev
```
