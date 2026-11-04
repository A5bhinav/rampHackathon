# 0016: Ingestion v0: custody, text, visibility, candidates, reader stub, grounding

**Owner:** Inesh  **Slice:** evidence  **Target day:** Thu Nov 5 (was Nov 2)

## Goal

`dialback_ledger.ingest(document, custody)` turns one document into the observation rows the
selection rule reads (spec 0015), following the pipeline in the ledger README: code runs every
stage except labeling. This serves R9 (document text is data: the labeler only labels values code
found, and code parses every value from a quote it checks), ADR-13 (the source type comes from
custody) and the visibility rule (hidden values are kept but never eligible).

Two PRs:

- **A, reading:** `custody.py`, `text.py` (the corpus page model: spans with position, size and
  colour, the facts pdfplumber reports per character), `visibility.py`, `candidates.py`
  (libphonenumber, strict matching).
- **B, labeling:** `flags.py` (`DIALBACK_READER`, safe default `stub`), `reader.py` (the stub: a
  keyword labeler over visible lines), `grounding.py`, `ingest.py`.

## Contract touched

None. Observations are dicts shaped like `ledger.observations` rows (spec 0013).

## Acceptance

A:
- [ ] An attachment's custody is its parent email's channel and receive time, whatever the
      fixture says. The document's SHA-256 is computed by code.
- [ ] A span is hidden if it's white (on the white page), under 4 points, or outside the page;
      `visibility` is `white`, `tiny`, `off_page` or `visible`.
- [ ] Candidates: every phone number libphonenumber finds (strict, US), as E.164, with its line,
      character offsets and the span's visibility. Invoice numbers, dates and "ending 4821" are not phones.
- [ ] **Corpus gate A:** for every frozen sighting in all 20 scenarios, a candidate in that
      document with that number and visibility (recall 100%), and no candidate that wasn't
      planted (precision 100%).

B:
- [ ] The labeler sees one document's visible text only, never hidden text, custody or other documents.
- [ ] Grounding keeps a label only if its quote appears in the visible text and contains a phone
      that code parses from the quote and that matches a visible candidate. The value comes
      from the quote, never from the labeler. Anything else is dropped and counted.
- [ ] A visible candidate the labeler skipped is still observed, with role and party `unknown`
      (so the selection rule can't pick it). Hidden candidates are observed with their visibility,
      role and party `unknown`, and no quote.
- [ ] `source_type` and `seen_at` come from custody.
- [ ] **Corpus gate B:** ingesting every corpus document, then running `select()`, reproduces
      every frozen expectation (the spec 0015 gate, end to end). The stub reader's role
      accuracy against the planted roles is reported, with every mismatch listed (design §7:
      "Reported"), and is at least 95%.
- [ ] A hostile labeler (wrong values, invented numbers, quotes from hidden text, instructions)
      gets 0 values accepted that the code didn't find in visible text.

## Tests (written first)

| Item | Test |
|---|---|
| A | `packages/ledger/tests/test_extract.py` |
| B | `packages/ledger/tests/test_ingest.py`, `test_grounding.py` |

## Out of scope

Reading real PDFs with pdfplumber, and OCR or vision for scans (the corpus can't be rendered to
PDF yet). The LLM reader (`DIALBACK_READER=llm`). Bank and routing candidates (Q44). Entity
resolution. Database writes (Q45).

## Open questions

- **Q47 (Abhi, Sami):** the stub reader labels by keywords ("Accounts receivable", "Billing",
  "Fax", "bank"). Is a keyword labeler acceptable as the `stub` for the demo, with the LLM
  reader behind `llm` once the 100-document labeled set exists?

## Commands

```bash
cd packages/ledger && uv run pytest -q
```
