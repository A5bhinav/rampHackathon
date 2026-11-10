# 0052: Generated documents for testing the reader and ingestion

**Owner:** Sami  **Partner:** Inesh (owns the ingestion code under test)  **Slice:** call and adversary  **Target day:** not set

## Goal

Today nothing tests the step where document text becomes labeled sightings:

- The fuzz lab (spec 0033) writes sightings directly, so it skips that step.
- The corpus (spec 0020) has 20 scenarios in one layout, `Label: number`, one number per line. That
  is exactly what the keyword stub reads well.
- FINDINGS D names a document generator as the first piece of red-teaming the reader. A1 and A2 both
  live in this step.

This spec adds a lab generator that:

1. writes documents with a known truth for every phone number, in many layouts and phrasings;
2. runs them through real ingestion (`ingest_all` with the configured reader), then `select()`;
3. checks properties any reader must have.

It runs on the stub today, and the same suite runs on the LLM reader (spec 0049) when it exists.

It serves R9 (document text is data), R2 (never call a number first seen in the change context),
FINDINGS A1 and A2, and FINDINGS D.

The lab only measures. It never edits `packages/ledger`. A finding goes to Inesh.

## Contract touched

None.

## Decisions

1. **A plan, then a document.** Code draws a plan and renders it. The plan is the truth.
   - **`DocPlan`:**
     - a document kind: invoice, vendor form, email body, signature block, remittance notice,
       listing snapshot, change-request email or contract page;
     - its custody: channel, sender, thread and subject, in the shapes spec 0033's worlds use;
     - filler lines;
     - a list of `NumberPlan`s.
   - **`NumberPlan`:**
     - the number, from the fictional block NPA-555-0100 to 0199;
     - its **owner:** the vendor, a bank, the customer, the attacker, or unrelated;
     - its **claim**, what the text says about it: a role (`main`, `ar`, `billing`, `fax`) and a
       party (`vendor`, `bank`, `customer`), each of which may be absent;
     - a phrasing id and a placement (see decision 2);
     - its format, visibility (`visible`, `white`, `tiny`, `off_page`), and whether a second number
       shares its line.
   - **Owner and claim differ on purpose.** An attacker's text can claim its own number is the
     vendor's. For the reader, the truth is the claim: a label is what the text says about a number,
     as spec 0049 defines it. For `select()`, the truth is the owner.
2. **Phrasings and placements are hand-written lists** in `evals/src/dialback_evals/docbank.py`.
   - **Phrasings,** each tagged with the role and party it claims. Examples: "Accounts receivable",
     "remittance questions", "for payment inquiries call", "Main office", "call us at", "Fax",
     "send forms by fax to", "verify with First Example Bank at", "our old number" (a bare mention,
     with no party), "your AP team at" (the customer's).
   - **Placements:** label then number on one line; number then label; the label on the line above;
     a table row (label and number in separate spans); a signature block; a sentence.
   - **Formats:** `(415) 555-0142`, `415-555-0142`, `415.555.0142`, `+1 415 555 0142` and
     `1-415-555-0142`.
   - **A `stub_style` tag:** a phrasing and placement is stub-style when the stub can read it (a
     label, then a colon, on the number's own line). Results are split by this tag.
   - The lists start from the corpus's own labels and spec 0049's hard cases (Q119).
3. **Rendering.**
   - Code lays out each line as a span with position, size and colour, as the corpus generator
     does.
   - Hidden numbers are hidden by colour, size or position, as `visibility.py` classifies them.
   - Each span is one line to ingestion (`text.py`), so a table row is a label span next to a
     number span.
   - The output is the exact document shape `ingest()` takes.
4. **From ingestion to selection, through one bridge.** The lab already turns `ingest_all`'s output
   into `select()`'s inputs in a test helper (`_ingest` in `evals/tests/test_generated_corpus.py`).
   That helper moves into a lab module, and both that test and this spec use it.
5. **The checks.** Every one must hold for any reader.
   - **G1. Extraction.** Every planned visible number is a candidate, there is no candidate that
     wasn't planned, and each hidden number has its planned visibility.

     A format that `candidates.py` misses (it uses `phonenumbers`, region US, `Leniency.VALID`) is
     an extraction finding for Inesh. It is not a reason to drop the format.
   - **G2. Hidden text is never labeled.** A hidden number gets no quote, role or party.
   - **G3. No unsafe label.** A number whose claimed party isn't `vendor` is never labeled party
     `vendor`. Gated.
   - **G4. A wrong party** (the reader's party contradicts the claimed party, and neither is
     unknown) is gated. **An over-block** (the claim is a vendor party with a role, but the label
     isn't) is counted and reported, split by `stub_style`. It is not gated: the stub over-blocks by
     design (A2), and spec 0049 is what judges whether a reader improves on that.
   - **G5. Rewording never flips a label (metamorphic).** Render the same plan with another phrasing
     of the same claim, another format or another placement. A number's label may go to or from
     unknown, but never from one known party to another. The share of labels that stay the same is
     reported.
   - **G6. A quoted earlier message changes nothing (metamorphic).** Adding a quoted earlier message
     (lines starting with `> `, with other numbers and labels) never changes the labels of the
     original numbers.
   - **G7. End to end through real ingestion.** The plans form a vendor world: the vendor's own
     documents, plus an attacker's change-request email and follow-ups whose text makes claims about
     numbers. After ingestion and `select()`:
     - text in the change context never moves the call to a different number (spec 0033's I12);
     - a number first seen there is never eligible (I1).

     Today's stub fails the first through A1. That failure goes on the known-failures list (spec
     0048, D3) with A1 as its finding.
6. **Search and findings.**
   - A Hypothesis strategy draws plans, using spec 0048's pinned draws.
   - A violation is shrunk to a minimal plan, frozen under `evals/docs-generated/` as the plan and
     its rendered documents, and re-run on every run.
   - `lab docs --examples 1000 --seed 0` runs in CI against the stub. The stub makes no network
     call, so this is fast.
7. **Against the LLM reader.**
   - `lab docs --reader llm --examples N --max-usd X` runs on demand only, never in CI, and stops at
     its dollar cap.
   - The model's output varies, so the results are rates over at least 3 repeats.
   - Generated documents can help write spec 0049's prompt. But they never stand in for its 70
     hand-written held-out documents, and a result on generated documents is never reported as
     that spec's evaluation.

## Acceptance

- [ ] Every generated document ingests without error, and its plan round-trips through the frozen
  JSON exactly.
- [ ] Over a fixed seeded sample, every phrasing, placement, format, visibility and owner appears,
  each at a stated floor.
- [ ] Each of G1 to G7 first fails on a deliberately broken double, then runs on the stub at seed 0
  with 1000 documents:
  - a reader that labels hidden text;
  - a reader that labels every number the vendor's;
  - a reader that flips bank and vendor;
  - a reader that reads the quoted earlier message as the document's own text;
  - a `candidates.py` stand-in that drops one format;
  - the context-blind rule from spec 0033 for G7.
- [ ] The stub's results are reported: unsafe labels, wrong parties, over-blocks split by
  `stub_style`, and the G5 stability rate. Its G7 failure (A1) is on the known-failures list.
- [ ] The bridge is one lab module, and `test_ingested_case_is_still_what_real_ingestion_produces`
  uses it unchanged.
- [ ] `lab docs --reader llm` refuses to start without `--max-usd`, and stops at the cap.
- [ ] Every number the generator writes is fictional, and no file under `packages/ledger` changes.

## Tests (written first)

| Item | Test |
|---|---|
| Shape, round trip, coverage | `evals/tests/test_docgen.py::test_documents_ingest`, `::test_plan_round_trips`, `::test_coverage_floor` |
| G1 to G7 | `evals/tests/test_docgen.py::test_each_check_catches_its_broken_double`, `::test_checks_on_the_stub` (marked `docs_gate`, deselected by the must-stay-green steps like `selection_gate`) |
| Stub report | `evals/tests/test_docgen.py::test_stub_report_has_every_count` |
| Bridge | `evals/tests/test_generated_corpus.py::test_ingested_case_is_still_what_real_ingestion_produces` (unchanged) |
| LLM run | `evals/tests/test_cli.py::test_lab_docs_llm_needs_a_cap` (with a fake reader) |
| Fictional numbers | `evals/tests/test_generated_corpus.py::test_lab_files_hold_only_fictional_numbers` (extended to `docs-generated/`) |

## Out of scope

- Prose written by a model around a plan from code (realism). That is a later spec, if these
  hand-written lists turn out too narrow.
- A red-team agent that writes attack documents. It needs the LLM reader first, and a non-Claude
  attacker model (the lab's rule that no model attacks or grades itself).
- Real PDFs, scans and email files.
- Any change to ingestion, the reader or `select()`.

## Open questions

These are my calls; Sami can overrule them.

- **Q119.** The phrase and placement lists start from the corpus's labels and spec 0049's hard
  cases. Inesh reviews them, since a list's gaps are this suite's blind spots.
- **Q120.** G3 (unsafe labels) and G4's wrong party are gated. Over-blocks are reported only.
- **Q121.** The generator lives in `evals`, not in `corpus/generator`. The corpus is Inesh's frozen
  truth, and this is the lab's search.
- **Q122.** This spec builds on spec 0048's pinned draws (C1) and known-failures list (D3). So its
  branch starts from `sami/lab-honest-impl` once those parts are pushed.

## Commands

```bash
cd evals && uv run pytest -q -m "not selection_gate and not report_gate and not docs_gate"
uv run lab docs --examples 1000 --seed 0
uv run --no-project --with ruff ruff check evals
```
