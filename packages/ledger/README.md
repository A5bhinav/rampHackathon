# packages/ledger: the Trust Ledger

**Owner: Inesh. Partner reviewer: Abhi.** Part of the evidence slice with `corpus/` and
the `ledger` schema.

A Python package that runs inside the core service, so the attack lab imports and tests
the exact same code. Models do one job here, labeling; code does everything else,
including the decision of which number to call.

## What's built (Nov 10)

| Piece | Where | Spec |
|---|---|---|
| Selection contract and the `stub` resolver | `stub.py`, `packages/contracts/schemas/selection.schema.json` | 0018 |
| The `ledger` schema | `supabase/migrations/ledger/` | 0019, 0024 |
| Frozen corpus: 12 patterns, 5 controls, the known limit | `corpus/` | 0020 |
| `ledger-select-v1` | `select.py`, `lookalike.py` | 0021 |
| Ingestion: custody, text, visibility, candidates, stub reader, grounding | `custody.py` … `ingest.py` | 0022 |
| Lineage panel | `apps/web/…/cases/[caseId]/lineage`, `reasons.py` | 0023 |
| In the database: `select_number`, `change_context`, writing ingestion | `store.py` | 0024 |
| The demo's ledger and `dialback-ledger demo` | `demo.py`, `demo_seed.json` | 0025 |
| Vendor risk list | `risk.py`, `apps/web/…/risk` | 0026 |
| The LLM reader (`DIALBACK_READER=llm`, default `stub`) and its evaluation script | `llm_reader.py`, `scripts/reader_eval.py` | 0049 |
| Real digital PDFs: read into the page model with visibility per character (`no_ink` included), `ingest_pdf`, `dialback-ledger read-pdf FILE` | `pdf.py`, `ingest.py` | 0092 (Part A) |

Not built yet: the LLM reader's prompt work against real documents and its scored run (the
100-document labeled set, spec 0049; the reader is built and tested on a faked API only), scans
and the pixel check on digital PDFs (spec 0092, Part B), entity resolution, `lineage()` as its own call (the panel reads the stored
selection), the ledger's nightly evals and ablations. Open questions for the team are in specs
0024 to 0026 (Q45, Q46, Q51 to Q53) and 0049 (Q107, Q108, Q110, Q123).

## Job

Record where every phone number in a vendor's history came from, then pick the number to
call, or refuse to pick one.

## Interface

| Call | Does |
|---|---|
| `ingest(document, custody)` | Adds a document and the values found in it |
| `select_number(case)` | Returns the chosen number with up to two alternates, or `no_provable_number`, plus every candidate and why it was or wasn't eligible. Contract: `packages/contracts/schemas/selection.schema.json` (yours to write) |
| `lineage(vendor_id)` | Feeds the dashboard's lineage panel |
| `risk_list()` | Feeds the opening screen of the demo |

The core worker calls `select_number` when it runs the `select_number` command and emits
`number_resolved {contact_point_id, number_selection_id, rule_version}` or
`no_provable_number`. Flag: `DIALBACK_RESOLVER=stub|ledger` (stub returns contract examples
for the Nov 1 walking skeleton).

## The pipeline (code runs every stage except labeling)

| Stage | What happens |
|---|---|
| Custody | Record how the document reached us (email, contract upload, vendor portal, listing snapshot, paper mail), our own receive time, the sender, the thread headers and a SHA-256. Attachments inherit their email's custody. |
| Text | pdfplumber extracts the text with each character's color, size and position (`pdf.py`, spec 0092), into the same page model the corpus uses. A page with no characters is a scan: refused while scans are off (spec 0092, Part B, reads it with local OCR and Claude's vision input). |
| Visibility | Flag white, tiny, off-page and no-ink text, per character. No ink: rendering the page with PDFium with and without the text changes nothing in its box (covered, invisible, clipped, or the colour of what's under it). Hidden values are kept, marked not visible to a person, and never eligible. |
| Candidates | libphonenumber finds phone numbers; code finds checksum-valid routing numbers and labeled account numbers. |
| Labeling (AI) | A quarantined model with no tools sees one document's visible text and returns JSON that must match a schema: each value's role (main, AR, billing, fax), its party (vendor, customer, bank) and the exact quote. Starts on Haiku 5.5; a 100-document labeled set decides on day 2 whether it needs Sonnet 5.5. Flag: `DIALBACK_READER=stub|llm`. |
| Grounding | Each quote must appear in the visible text and match a candidate. Code parses the value from the quote, not from the model's output; anything else is dropped (OWASP LLM09: zero ungrounded values accepted, by construction). |
| Entities | Documents attach to vendors by tax ID and domain. Fuzzy name matches wait for a person to confirm; lookalike domains are never merged. |
| Observations | Append-only rows. `source_type` comes from custody metadata, never from the model. |

## Independent sources (ADR-13)

A source type is how a value reached us, not what kind of document it was. All email counts
as one source, however many mailboxes or domains it came from. A signed contract our staff
stored, a vendor-portal entry, a public listing and paper mail each count once. The old
vendor master's "number on file" is shown but never counted.

## The selection rule, `ledger-select-v1`

Pick the oldest number that:

- appears in at least two independent source types before the cutoff;
- was first seen at least 90 days before the cutoff;
- did not first appear in the change context;
- isn't one digit off, or two swapped digits away from, an older vendor number, unless both are long-established lines.

Ties go to the number with more source types, then to an AR or billing line over the main
line. If nothing qualifies, the case goes to needs_review and no call is made. Alternates
are used only if the first number is dead.

- **Cutoff:** the earliest of the request's arrival, the first message from a lookalike domain, and the change appearing in Ramp. All times are our own receive times, never a printed date or an email's `Date:` header.
- **Change context, deliberately broad:** the request's thread, the same subject line within 30 days, anything from a lookalike domain, anything received after the cutoff.
- **Replayable:** a selection reads only rows recorded before the case opened, and stores the rule version, its settings and a hash of the rows it used.
- **R2 in practice:** a number is rejected if it *first* appeared in the change context. A long-established number that the thread happens to repeat stays eligible.

**Known limit, stated on the scorecard:** a fraudster who has controlled a vendor's mailbox
and a public listing for more than 90 days, at a vendor with no stored contract, can get
their number picked.

## Data you own (`ledger` schema, `supabase/migrations/ledger/`)

| Table | A row is | Key fields |
|---|---|---|
| `documents` | One source file: invoice, contract, email, W-9, listing | vendor_id, kind, sha256, custody_channel, received_at (our clock), claimed_date (shown only, never trusted), sender, thread_id, storage_path |
| `observations` | "Value X was seen in document D on date T" | document_id, vendor_id, kind (phone, bank account, email, domain), value_normalized, role, quote, visibility, source_type, seen_at, extractor_version |
| `contact_points` | One phone number per vendor with its lineage summary | vendor_id, e164, first_seen_at, source_types, status (eligible, excluded), exclusion_reason |
| `number_selections` | Which number a case will call, and why | case_id, contact_point_id or none, candidates (every number with its reason), rule_version |

`vendor_id` references `app.vendors(id)`; `case_id` references `app.cases(id)`. Every table
carries `dataset_id`. `scripts/db/apply.sh` applies your folder after `app`.

## Properties (Hypothesis) and evals you owe

1. Adding observations inside the change context never makes a number eligible.
2. The outcome depends only on rows recorded before the case opened.
3. Shuffling the observations doesn't change the outcome.
4. No number whose earliest dated sighting is in the change context is ever selected.
5. Role and party labels can reorder or remove eligible numbers, but never make a number with fewer than two source types eligible.
6. No selected number is one digit off an older candidate unless it is an established sibling line.

Ledger evals run nightly against labels written by the same code that planted each value,
plus 100 documents checked by hand: extraction precision/recall/F1 by document kind,
grounding (0 ungrounded accepted), visibility, poison detection per pattern (every miss
listed), selection (0 poisoned dials; correct and false refusals), and ablations (minimum age
0/30/90/180 days, near-miss distance 1 or 2, the three ways of counting email).

## Your days

| Day | Deliverable |
|---|---|
| Sat Oct 31 | Generator v0; poisoning and control cases written, expected outcomes frozen and hashed (`corpus/`) |
| Sun Nov 1 | A `select_number` stub that returns contract examples; demo seed v0 |
| Mon Nov 2 | Reader with grounding and visibility checks; injection fixtures |
| Tue Nov 3 | Selection rule with its property tests; entity resolution |
| Wed Nov 4 | Lineage panel (`apps/web`) |
| Thu Nov 5 | Risk list for the opening screen; ledger evals |
| Fri Nov 6 | Ablations; 100 documents checked by hand; held-out scenarios |
| Sat Nov 7 | Fixes from the ledger evals |
| Sun Nov 8 | Demo seed frozen |
| Mon Nov 9 | Lineage walkthrough for the video |
| Tue Nov 10 | Check that the README works from a clean clone |

## Start here

1. Write `packages/contracts/schemas/selection.schema.json` with two examples (a pick, and `no_provable_number` with the candidates and reasons). Abhi's worker consumes it from day 1.
2. Write `supabase/migrations/ledger/0001_ledger.sql` for the four tables and run `scripts/db/test.sh`.
3. `src/dialback_ledger/`: `custody.py`, `text.py` (pdfplumber per-character), `visibility.py`, `candidates.py`, `reader.py` (quarantined labeler behind `DIALBACK_READER`), `grounding.py`, `entities.py`, `select.py` (`ledger-select-v1`), `lineage.py`.
4. Property tests in `tests/test_select_properties.py` before `select.py`.
