# 0052: Generated documents for testing the reader and ingestion

**Owner:** Sami  **Partner:** Inesh (owns the ingestion code under test)  **Slice:** call and adversary  **Target day:** not set

## Goal

Nothing tests the step where document text becomes labeled sightings:

- The fuzz lab (spec 0033) writes sightings directly, so it skips that step.
- The corpus (spec 0020) has 20 scenarios in one layout, `Label: number`, one number per line. That
  is exactly what the keyword stub reads well.
- FINDINGS D names a document generator as the first piece of red-teaming the reader, and A1, A2,
  A19 and A20 all live in this step.

This spec adds a lab generator that:

1. writes documents with a known truth for every phone number, in many layouts and phrasings;
2. runs them through real ingestion, with an explicit reader, then `select()`;
3. checks properties any reader must have.

It runs on the stub today. The same suite runs on the LLM reader (spec 0049) once that reader
exists.

It serves R9 (document text is data), R2 (never call a number first seen in the change context),
and FINDINGS A1, A2, A19, A20 and D.

The lab only measures. It never edits `packages/ledger`. A finding goes to Inesh.

## Contract touched

None.

## Decisions

1. **A plan, then a document.** Code draws a plan and renders it. The plan is the truth.
   - **`DocPlan`:**
     - a document kind: invoice, vendor form, email body, signature block, remittance notice,
       listing snapshot, change-request email or contract page;
     - its **author:** the vendor, a bank, the customer, an attacker posing as the vendor, or an
       unrelated party;
     - its custody: channel, sender, thread and subject;
     - filler lines;
     - an optional quoted earlier message, which has its own author;
     - a list of `NumberPlan`s.
   - **`NumberPlan`:**
     - the number;
     - its **owner:** the vendor, a bank, the customer, the attacker, or unrelated;
     - its **claim**, what the text says about it: a role (`main`, `ar`, `billing`, `fax`) and a
       party (`vendor`, `bank`, `customer`), each of which may be absent;
     - its phrasing, placement, format and visibility (`visible`, `white`, `tiny`, `off_page`);
     - whether a second number shares its line.
   - **A phrasing that names no party claims its author's party.** "Main office", "Fax" and "call us
     at" in a bank's notice claim the bank. An attacker posing as the vendor claims the vendor. A
     number inside a quoted message carries the quoted author's claim. This is spec 0049's meaning of
     a label: what the text says about a number.
   - **Owner and claim differ on purpose.** An attacker's text can claim its own number is the
     vendor's. For the reader, the truth is the claim. For `select()`, the truth is the owner.
2. **Hand-written lists** in `evals/src/dialback_evals/docbank.py` (Q119).
   - **Phrasings,** each with the role and party it states, if any. Examples: "Accounts
     receivable", "Remittance questions", "for payment inquiries call", "Main office", "call us at",
     "Fax", "send forms by fax to", "verify with First Example Bank at", "Customer main line", "our
     old number" (a bare mention).
   - **Placements:**
     - label, then number, on one line;
     - number, then label;
     - the label on the line above;
     - a table row (a label span next to a number span; each span is its own line to `text.py`);
     - a signature block;
     - a sentence;
     - a line with a clock time before the number (A20).
   - **Formats:** `(415) 555-0142`, `415-555-0142`, `415.555.0142`, `+1 415 555 0142` and
     `1-415-555-0142`.
   - **Area codes** come only from a list checked to parse in all five formats. 415 and 469 are on
     it. `worldgen.FAR_NPAS`'s 756 parses in none, so it is left out.
   - **Stub-readable** is computed, not hand-tagged. It means the stub, given the line alone,
     returns the claimed role and party. Results are split by it.
3. **Rendering.** Code lays out each line as a span with position, size and colour, as the corpus
   generator does. Hidden numbers are hidden by colour, size or position, as `visibility.py`
   classifies them. The output is the exact document shape `ingest()` takes.
4. **One bridge from ingestion to selection.**
   - A lab module converts a set of documents into `select()`'s inputs: the change request's
     document id, the vendor's domains, the times, and an explicit reader.
   - The fixed helper in `evals/tests/test_generated_corpus.py` becomes a call to it, and its test
     passes unchanged.
5. **The checks.** Every one must hold for any reader. Each one, except G7, first fails on a
   deliberately broken stand-in, then runs on the stub.
   - **G1. Extraction.** Every planned visible number is a candidate, there is no candidate that
     wasn't planned, and each hidden number has its planned visibility.
     - **Stand-in:** a `candidates.py` replacement that drops one format.
     - A format the real `candidates.py` misses is an extraction finding for Inesh. It is not a
       reason to drop the format.
   - **G2. Hidden text is never labeled.**
     - This holds by ingestion's construction: the reader only sees visible lines, and grounding
       only matches visible lines (spec 0022). So the check is a regression guard.
     - **Stand-in:** in the lab's own test, an ingestion that hands hidden lines to the reader.
   - **G3. No unsafe label.** A number whose claim isn't the vendor is never labeled party `vendor`.
     - **Stand-in:** a reader that labels every number the vendor's.
   - **G4. No wrong party.** The label's party never contradicts the claimed party, when neither is
     unknown.
     - **Stand-in:** a reader that swaps bank and vendor.
     - **Over-blocks** (a vendor claim with a role, left unlabeled) are counted and reported, split
       by stub-readable. They are not gated, because the stub over-blocks by design (A2) and spec
       0049 judges whether a reader improves on it.
   - **G5. Rewording never flips a party (metamorphic).**
     - Render the same plan with another phrasing of the same claim, another format or another
       placement. A label may go to or from unknown, but never from one known party to another.
       The share of labels that stay the same is reported.
     - **Stand-in:** a reader whose party depends on the number's format.
   - **G6. A quoted earlier message never changes the original numbers' labels (metamorphic).**
     - Numbers inside the quote are judged by G3 and G4 against the quoted author's claim.
     - **Stand-in:** a reader that takes the nearest label anywhere in the document.
   - **G7. End to end, change-context text never moves the call.**
     - **The world:**
       - the vendor's own records, in stub-readable placements, so its real number is eligible
         on the stub;
       - in half the worlds, a planted provable number: 2 counting channels, at least 90 days old,
         labeled the vendor's;
       - the attacker's change-request email and follow-ups, with varied text.
     - **The check:**
       - relabel every change-context sighting ingestion produced as the vendor's, and the call
         list must not change (spec 0033's I12, removal half only, so only labels ingestion
         actually produced count);
       - no number first seen in the change context is eligible (I1).
     - **Showing it can fail:** today's rule fails it on the frozen A1 world, and spec 0033's
       context-blind rule passes it.
6. **Gating the stub: known failures by pattern.** The stub breaks G3 and G4 by design. It labels
   any role word as the vendor's: "Customer main line" comes back as the vendor's main line (A19),
   and so do the words before a clock time (A20).
   - This uses spec 0048 D3's strict known-failures list, with entries keyed by pattern: the check,
     the phrasing and the placement, each naming its FINDINGS item.
   - A listed pattern must still fail. When it stops failing, the job turns red so the entry is
     taken off.
   - A new pattern that fails turns the job red.
   - G7's failure (A1) is listed the same way.
7. **A fixed sample, not a search.**
   - Plans come from a seeded `random.Random`, not Hypothesis. So a seed gives the same documents
     everywhere (FINDINGS B10), and every plan is checked: nothing stops at the first failure (B3).
   - A violation is shrunk at the plan level until removing any single number, filler line or
     document, or simplifying any phrasing or format, makes it go away. It is frozen under
     `evals/docs-generated/` as the plan and its rendered documents, and re-run on every run.
   - `lab docs --examples 1000 --seed 0` runs in CI against the stub, which makes no network call.
8. **CI.** The must-stay-green steps in `lab-fuzz.yml` and `lab-smoke.yml` deselect the new
   `docs_gate` tests, as they do `selection_gate`. A `lab-docs` job runs them with the
   known-failures list. It triggers on `packages/ledger` and `evals`.

## Acceptance

- [ ] Every generated document ingests without error, and its plan round-trips through the frozen
  JSON exactly.
- [ ] Over the seed-0 sample, every phrasing, placement, format, visibility, author and owner
  appears, each at a stated floor.
- [ ] Each of G1 to G6 fails on its stand-in. G7 fails on today's rule with the A1 world, and passes
  with the context-blind rule.
- [ ] On the stub at seed 0 with 1000 documents, every failure matches a listed pattern, and every
  listed pattern still fails.
- [ ] The stub's report gives unsafe labels, wrong parties, over-blocks split by stub-readable, and
  the G5 stability rate.
- [ ] The bridge is one lab module, and `test_ingested_case_is_still_what_real_ingestion_produces`
  passes unchanged through it.
- [ ] Every phone number in the generated plans and rendered documents is fictional. The check
  scans the rendered text with `phonenumbers`, not an E.164 pattern. No file under
  `packages/ledger` changes.

## Tests (written first)

| Item | Test |
|---|---|
| Shape, round trip, coverage | `evals/tests/test_docgen.py::test_documents_ingest`, `::test_plan_round_trips`, `::test_coverage_floor` |
| G1 to G7 | `evals/tests/test_docgen.py::test_each_check_catches_its_stand_in`, `::test_g7_fails_today_and_passes_context_blind`, `::test_checks_on_the_stub` (`docs_gate`) |
| Known failures by pattern | `evals/tests/test_docgen.py::test_unlisted_pattern_turns_red`, `::test_listed_pattern_that_passes_turns_red` |
| Stub report | `evals/tests/test_docgen.py::test_stub_report_has_every_count` |
| Bridge | `evals/tests/test_generated_corpus.py::test_ingested_case_is_still_what_real_ingestion_produces` (unchanged) |
| Fictional numbers | `evals/tests/test_docgen.py::test_rendered_text_holds_only_fictional_numbers` |
| CI | `evals/tests/test_ci_wiring.py` |

## Out of scope

- **Running against the LLM reader.** It needs spec 0049's reader and its model client, so that a
  run can count its own cost and stop at a cap. That's a follow-up once 0049 lands.
- **Prose written by a model** around a plan from code.
- **A red-team agent** that writes attack documents. It needs the LLM reader first, and an attacker
  that isn't Claude (the lab's rule that no model attacks or grades itself).
- Real PDFs, scans and email files.
- Any change to ingestion, the reader or `select()`.

## Open questions

These are my calls; Sami can overrule them.

- **Q119.** The phrase and placement lists start from the corpus's labels and spec 0049's hard
  cases. Inesh reviews them, since a list's gaps are this suite's blind spots.
- **Q120.** The stub is gated by known failures keyed by pattern (decision 6), extending Sami's
  Q101 decision from cases to patterns.
- **Q121.** The generator lives in `evals`, not in `corpus/generator`. The corpus is Inesh's frozen
  truth, and this is the lab's search.
- **Q122.** This builds on spec 0048's known-failures list (D3). So it starts from
  `sami/lab-honest-impl` once D3 is pushed. It doesn't need spec 0046.

## Commands

```bash
cd evals && uv run pytest -q -m "not selection_gate and not report_gate and not docs_gate"
uv run lab docs --examples 1000 --seed 0
uv run --no-project --with ruff ruff check evals
```
