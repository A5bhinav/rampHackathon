# 0092: Real PDFs and scans: render them, read them back

**Owner:** Sami (the reader is in `packages/ledger`, the renderer in `evals`)  **Slice:** evidence  **Target day:** Part A now, for the Nov 19 final; Part B after it (Q156)

## Goal

Every document today is a page model: JSON spans with position, size and colour, the facts
pdfplumber reports per character (spec 0022). Nothing reads a real PDF or a scan. So the first
step a real document takes is untested, and so are the hiding tricks a real PDF allows beyond
white, tiny and off-page text. Spec 0022 left this out because "the corpus can't be rendered to
PDF yet".

This spec adds both, in two parts that land separately:

- **Part A, digital PDFs, no model.** A lab renderer writes any page model to a real PDF. A reader
  in `packages/ledger` reads a PDF back into the page model, with visibility decided per
  character, so `ingest()` and everything after it is unchanged.
- **Part B, scans.** The renderer also writes scans: page images with scan damage. The reader
  reads a scan twice, with local OCR and with Claude's vision input, and a phone number counts
  only when both readings agree on every digit. The same local OCR checks the digits of every
  phone number in a digital PDF against the page's pixels.

The labeler (the stub, or spec 0049's LLM reader) still reads text. This spec is the step that
turns a file into that text.

It serves:

- **R7:** anything the reader can't account for fails closed.
- **R9 and ADR-4:** document text is data, and models read while code decides. No model's
  transcription becomes a phone number on its own.
- **ADR-14:** PDFs are read locally with per-character checks, and scans go to Claude's vision
  input. Part B amends ADR-14 (Q158, Sami, Nov 11): a scan's text comes from local OCR, and the
  vision call only confirms its phone digits. Part B's first PR updates ADR-14's row in
  `docs/adr/README.md`.
- **The visibility rule:** hidden values are kept, marked, and never eligible (`select.py` counts
  only `visible` sightings).

## Contract touched

One contract PR lands before Part A's code (contracts only grow). It holds:

- **`supabase/migrations/ledger/`:** the enum `ledger.visibility` grows two values.
  - `no_ink`: a person can't see it, because drawing it leaves no mark on the page (Part A).
  - `unverified`: its digits couldn't be confirmed (Part B).
- **The selection contract** (`packages/contracts/schemas/selection.schema.json` and
  `packages/contracts/openapi/dialback.yaml`):
  - The description of `hidden_text_only` widens to "white, tiny, off the page, or no ink". A
    number seen only in `no_ink` text gets that reason (Part A).
  - A new reason, `unconfirmed_digits_only`, covers a number seen where a person could see it,
    but only in text whose digits couldn't be confirmed (Part B). Its sentence goes in
    `reasons.py`.
  - `store.change_context` treats it like `hidden_text_only`: the number was never confirmed
    where a person could see it.

Observations and documents keep their columns.

## Decisions

### Part A: digital PDFs

1. **The page model stays the interface.**
   - `dialback_ledger.pdf.read_pdf(data: bytes) -> list[dict]` returns `pages` in the page model
     `text.py` reads: `{width, height, spans: [{text, x, y, size, color}]}`.
   - The units: `x` is the span's first character's left edge and `y` is pdfplumber's `top`,
     both in points from the page's top-left. `color` is `#rrggbb`.
   - A span may also carry `ink: false`, which `visibility.classify` reads as `no_ink`, after
     the existing classes.
   - `ingest_pdf(data, document, *, extractor_version, reader, result)` builds the document
     from those pages and calls `ingest()`. `document` is everything but `pages`. Nothing after
     text changes.
2. **Lines and spans.**
   - Characters (pdfplumber's `page.chars`) group into lines by baseline, in x order.
   - A line splits into spans where the gap between two characters is wider than the builder's
     word-gap threshold, so a table row's cells are separate spans, as the page model writes
     them. It also splits wherever the visibility class changes.
   - So a span never mixes classes. A hidden character inside a visible number breaks it into
     visible fragments, none of which is a phone, so no candidate ever spans a hidden character.
3. **Hidden, per character.** A character is hidden if any of these holds. Its span takes the
   first one that applies, in this order:
   - `off_page`: its box isn't inside the page's crop box.
   - `tiny`: under 4 points.
   - `white`: its fill colour, converted from gray, RGB or CMYK to RGB, has every channel at
     `F0` or above. A colour the reader can't convert (a pattern, a separation, an ICC profile it
     can't read) counts as white (R7).
   - `no_ink`: drawing the span leaves no mark. The page is rendered with PDFium (`pypdfium2`,
     which pdfplumber already depends on) twice: as it is, and with only the text objects that
     draw this span removed. If the two renders don't differ inside the span's box, the span
     has no ink. Whitespace is exempt. A span the reader can't isolate this way counts as
     `no_ink` (R7).

   Comparing with and without the span, rather than the box against its surroundings, is what
   catches text drawn over other ink. The ink rule catches five things:
   - text under a filled shape or an image drawn after it;
   - text in invisible render mode (mode 3), including over a black bar its own size;
   - text clipped away;
   - an invisible number set so that it overlaps a visible line's glyphs;
   - white text over a shape the same white.

   The resolution and pixel threshold are the builder's, pinned by two tests: every visible
   span in every round-trip document has ink, and every planted hidden one has none.
4. **What the reader refuses.** The document fails closed with `PdfRefused(reason)`: nothing of
   it is stored, as with spec 0049's `ReaderFailed`. It refuses:
   - an encrypted or unparseable file;
   - over 20 pages or 10 MB;
   - reading that takes over 20 seconds;
   - a page with annotations or form fields that hold text, since a person may see text that
     the text layer lacks;
   - a page with no characters at all, which is a scan (Part B), while scans are off.

   Two things are read but found to hold nothing:
   - **Unmapped glyphs:** characters that extract as `(cid:N)` are kept as text, but hold no
     digits.
   - **Images:** images on a digital page aren't read. A number shown only in an image is never
     a candidate, so it can't be called.
5. **Hash and version.**
   - **`sha256`:** for a PDF it's the hash of the file's bytes, computed by `ingest_pdf` (the
     custody fact).
   - **`extractor_version`:** `pdf-<reader version>/<labeler version>`, so a stored row says
     which text reader and which labeler made it.

   Page-model documents keep today's values.
6. **The lab renderer.** `evals/src/dialback_evals/pdfrender.py` is lab-only; the product never
   imports it.
   - It writes any page model to a PDF with reportlab: one text object per span, at its x, y,
     size and colour.
   - The font must cover every character in the corpus, the demo seed and spec 0052's banks. A
     character it can't draw fails the render, and a test pins this.
   - Optional span fields, read only by the renderer, plant the tricks a page model can't
     express: `trick: cover_rect | cover_image | invisible | clipped`. The ledger's
     page-model path ignores `trick`, so the PDF reader has to find these from the PDF itself.
   - A trick span's expected visibility is `no_ink`.
   - A page model's `scan` flag is ignored in Part A: C04's scanned contract is rendered as a
     digital PDF, so C04 keeps its second source type. Part B renders `scan: true` documents
     as scans (D11).
   - **One precondition, and a fix to spec 0052's renderer.** No two spans on a page may
     overlap. A test checks this for every source and names any page that breaks it.
     Spec 0052's `docgen.render` breaks it today: `HIDE["off_page"]` moves every span of a
     hidden row to the same x, so an off-page table row's label and number sit on top of each
     other.
     - **The fix:** Part A's first PR changes that rule to move a hidden row's spans by one
       offset, keeping their relative x.
     - **Re-run:** `lab docs` runs again at its seeds, and any change to its patterns is
       reported. The frozen cases hold no hidden spans, so they don't change.
7. **Round trip is the main check.** Every page model is rendered, then read back:
   - **Sources:** the corpus (20 scenarios), the demo seed, and spec 0052's generator (its frozen
     cases and seeds 0 to 4, once #125 lands).
   - **The check:** the spans must come back the same. Same text, same visibility, the same
     colour, x and y within 0.5 pt, and size within 0.1 pt.
   - **The bar:** it's exact. One wrong span fails it.
   - **Showing it can fail:** a stand-in reader that skips the colour check must fail on P04a,
     and one that skips the ink check must fail on the planted covers.
8. **The same results through PDFs.**
   - **Spec 0022's gates:** corpus gates A and B still pass when every document goes through
     render, then `ingest_pdf`.
   - **`lab docs` (spec 0052):** at seed 0 it gives the same pattern set through the PDF path as
     through the page model.
9. **Demo and a command.**
   - **The demo's PDFs:** the lab renders the demo seed into
     `packages/ledger/src/dialback_ledger/demo_pdfs/`, and `lab pdf render --check` keeps them
     current, like the corpus generator's `--check`.
   - **`dialback-ledger demo load --pdf [DIR]`:** loads those PDFs instead of the JSON, or the
     PDFs in `DIR` when given. It writes the same observations as `demo load`, apart from
     `sha256` and `extractor_version`.
   - **A live run with a team phone.** The product can't re-render a PDF, so the swap moves to
     render time:
     - `lab pdf render --demo --phone acme=+1... --out DIR` writes the demo seed's PDFs with
       the team phone already in them. `DIR` must be outside the repo, because team phones are
       never committed.
     - `demo load --pdf DIR` then reads them, so each stored hash is the hash of the file
       actually read, and its rows match it.
     - `--pdf` with `--phone` is refused, since the phone is already in the PDFs.
     - `dialback-core demo prepare` passes `--pdf DIR` through. The change request stays JSON:
       it's an email, not a PDF.
   - **`dialback-ledger read-pdf FILE`:** prints each line with its visibility, plus the phone
     candidates code found. It labels and stores nothing.
10. **The glyphs can say one thing, the text layer another.**
    - **The case:** a font whose character map is altered shows one digit and extracts another.
      There is ink, but not the digits the text layer holds.
    - **The plant:** the lab plants one, with two digit glyphs swapped using fontTools on a font
      whose license allows changes.
    - **Its listing:** it was listed as a strict known failure, in spec 0048 D3's manner, with
      its finding. The legibility check (D10a) closes it, so it comes off the list.
    - **Not in this case:** an image drawn over text is caught by the ink rule (D3), because
      removing the covered text changes nothing on the page.

10a. **The legibility check, moved up from Part B (Sami, Nov 11).**
    - **Why.** D3's ink rule asks "did anything change on the page?", which isn't "can a person
      read this?". The first review found 5 ways past it, and those were patched. A red team
      then found 15 more (#156). Sami chose to stop patching rule by rule and read the pixels.
      D3 and its patches stay: they classify all text, cheaply. This check is added on top.
    - **The rule.** Every `visible` phone candidate in a digital PDF must pass all four tests
      below, measured on the page PDFium renders, never on the text layer. If any fails, or OCR
      errors or times out, its span is `unverified`: kept, never eligible, no quote, and a
      number seen only this way gets `unconfirmed_digits_only`.
      1. **Contrast.** The digits' ink against its local background has a contrast ratio of at
         least 3:1 (the WCAG ratio for large text).
      2. **Size.** The digits measure, in pixels, at least what a 4 point font's digits measure
         (`TINY_POINTS`). Size comes from the pixels because the text layer's size can lie.
      3. **Together.** No gap between the number's inked digits is wider than 2 digit heights,
         so a number split across a line can't read as one.
      4. **Read.** Tesseract 5 (local, through `pytesseract`) reads the same digits, in the same
         order, with a confidence of at least `C_MIN`.
    - **OCR must not out-read a person.** OCR binarizes adaptively, so left alone it would read
      `#FFFFEE` on white perfectly. So test 1 comes first, and OCR gets only an image in which
      pixels below the contrast floor are set to the background. Upscaling for OCR is allowed;
      it adds no information.
    - **Settings.** The render resolution, the crop margin and `C_MIN` are the builder's, and
      are development settings: they're chosen with #156's and the first review's attacks in
      view. The 3:1 ratio, the 4 point floor and the 2-digit-height gap are set here, now.
    - **Time.** OCR counts inside D4's 20 seconds per document.
    - **Installs.** `pytesseract` in `packages/ledger`; `tesseract-ocr` in the core image and in
      CI. Without Tesseract, reading a PDF stops with `ReaderUnavailable` (R7). So
      `demo load --pdf` needs Tesseract on the machine that runs it.
    - **Stated limits, each with a finding:**
      - PDFium is the only renderer judged, so #156's 4 cases that only another viewer covers
        stay. They're listed as strict known failures.
      - Only digits are checked. An illegible label beside a legible number can still steer the
        labeler.

### Part B: scans

11. **What a scan is:**
    - a page with no characters and one image covering at least 90% of it;
    - a PNG, JPEG or TIFF file;
    - a page whose only text is in invisible render mode over a full-page image. That's someone
      else's OCR, and it's ignored.

    The corpus's C04 scanned contract goes through the scan path in Part B's tests. Corpus gate
    B runs it in CI with real Tesseract and a faked vision call that returns the page's true
    lines; live vision calls happen only in the scored run.
12. **Two readings.**
    - **The text layer comes from local OCR.**
      - The engine: Tesseract 5 through `pytesseract`, in English, run locally, with each word's
        box and confidence.
      - The page model: the words, grouped into lines, become the scan's page model. Position
        and size come from the boxes, and the colour is `#000000`.
      - Why: OCR has no instructions to follow, and nothing it reads leaves the machine.
    - **Claude's vision input only checks.**
      - The call: one quarantined call per page, with no tools. It returns schema-checked JSON,
        `{lines: [string]}`: transcribe every line exactly, and label nothing.
      - Settings, as in spec 0049: the model is pinned (`claude-haiku-5-5`), effort is low, and
        there's a timeout.
      - Failure: an error or an off-schema reply fails the document closed.
      - Redaction first: before the image is sent, the boxes of OCR words holding 7 or more
        digits that aren't part of a phone candidate are filled black, as spec 0049 redacts text.
        This is best effort, not a guarantee: a digit run that OCR misses or misreads, likelier
        on `fax` and `photo`, reaches the API unredacted. The lab measures how often (D17).
        Every document is fictional today. A real deployment needs a decision on this first
        (out of scope).
      - Where its output goes: its transcription never reaches the labeler and is never stored.
13. **The agreement rule.**
    - **Visible:** a phone candidate that code finds in the OCR page model stays `visible` only
      if both hold:
      - every OCR word holding its digits has a confidence of at least `C_MIN`;
      - code finds the same E.164 number on the matching line of the vision transcription. The
        matching line is the vision line most like the OCR line, by normalized edit similarity,
        above a floor the builder sets on development data.

      A match anywhere on the page isn't enough: P01 and P08 put near-identical numbers on one
      page, and change emails say "new X (was Y)". The matching is also count-aware, so a number
      OCR finds twice needs two matches.
    - **Otherwise unverified:** its span becomes `unverified`. It's kept, it's never eligible,
      it has no quote, and a number seen only this way gets `unconfirmed_digits_only`.
    - **Vision only:** a number that only the vision call reads is not a candidate. It's counted
      as dropped, with the reason "vision only".
    - **`C_MIN`:** set on development data, then frozen before the held-out run.
14. **The pixel check on digital PDFs** moved to Part A as D10a (Sami, Nov 11). Part B reuses
    its OCR and its `C_MIN`.
15. **Flags and installs.**
    - **`DIALBACK_SCANS=off|on`:** the default is `off`. With it off, a scan page fails its
      document closed with `PdfRefused("scan")`. With it on, both Tesseract and
      `ANTHROPIC_API_KEY` are needed, or the run stops at startup with `ReaderUnavailable`.
    - **The legibility check (D10a):** always on, from Part A.
16. **The lab's scans.** `pdfrender` rasterizes each rendered page with PDFium, then applies one
    of four damage profiles. Each is deterministic by seed:
    - `clean`: 300 dpi;
    - `office`: 200 dpi, rotation up to 1°, mild noise, and JPEG;
    - `fax`: 204 by 98 dpi, black and white, with speckle;
    - `photo`: perspective, blur and uneven light.

    The truth is the plan's, so every digit can be scored.
17. **The bars, set now, for Part B's scored run.** These are fixed before any data exists.
    - **The documents:**
      - Development: spec 0052's `draw_docs(examples=200, seed=s)` for seeds 0 to 4.
      - Held out: the same at seed 1000, never run before: 200 documents.
      - Each document is rendered at every profile, with its damage drawn from the same seed,
        and the held-out set runs 3 times, because the vision call varies.
    - **Matching to the truth is by position, not by value.** Each true visible number's span
      has a box from the plan. A candidate matches the true number whose box overlaps its OCR
      box. A `visible` candidate with no overlapping true number, or with digits that differ
      from the one it overlaps, is unsafe. So a misread that happens to give another planted
      number still counts as unsafe.
    - **No unsafe digits, on all four profiles:**
      - in every run, no unsafe `visible` candidate on `clean`, `office`, `fax` or `photo`;
      - production can't tell which profile a scan is, so one unsafe digit anywhere keeps scans
        off;
      - zero of N only bounds the true rate below about 3/N (the rule of three), so the report
        states N for each profile.
    - **Over-blocks:** a true visible number that doesn't end up as a `visible` candidate with
      the right digits is an over-block. That includes numbers OCR never found, so missing
      numbers can't pass the bar.
      - At most 2% on `clean` and 10% on `office`, in every run.
      - `fax` and `photo` are reported only.
    - **No new wrong-party labels:** through the scan path, the labeler (the stub, and the LLM
      reader if it's switched on) shows no G3 or G4 pattern (spec 0052) that the same document
      doesn't show through its digital PDF. OCR garbling a party word must not turn another
      party's line into the vendor's.
    - **The pixel check:** on Part A's round-trip documents it leaves no visible number
      `unverified`, and it catches the decision-10 plant.
    - **Reported, exploratory:**
      - OCR alone and vision alone, per profile;
      - how often a non-phone run of 7 or more digits reached the API unredacted (D12).

    If a bar fails, scans stay off. Changing a bar or `C_MIN` after the held-out run needs fresh
    held-out seeds, and the first result is reported next to the new one.

## Acceptance

Part A:
- [ ] The contract PR covers both halves:
  - `no_ink` and `unverified` are added to `ledger.visibility`;
  - in the selection contract, `hidden_text_only`'s description widens and
    `unconfirmed_digits_only` is added.

  The migration tests and the contracts validator pass, and nothing else changes.
- [ ] No two spans overlap in any round-trip source, after the `docgen` off-page fix (D6).
- [ ] Round trip (D7) is exact for every corpus document, every demo seed document and every
      spec 0052 case and seed listed. Both stand-ins fail it.
- [ ] Each hiding trick is classed correctly, with one planted test each:
  - white and near-white (`#f4f4f4`), in gray, RGB and CMYK;
  - an unconvertible colour;
  - tiny;
  - off the page past each edge;
  - covered by a rectangle, and covered by an image;
  - invisible render mode, alone and over a black bar its own size;
  - clipped;
  - an invisible number overlapping a visible line;
  - white over a white shape;
  - a hidden digit inside a visible number, which gives no candidate.
- [ ] A number seen only in `no_ink` text is refused with `hidden_text_only`.
- [ ] Every refusal reason in D4 has a test, and none stores anything.
- [ ] Corpus gates A and B pass through the PDF path, with C04's scanned contract rendered as
      a digital PDF. `lab docs` at seed 0 gives the same patterns through it, and any change
      from the `docgen` fix is reported.
- [ ] Demo:
  - `demo load --pdf` writes the same observations as `demo load` (D9);
  - `--pdf` with `--phone` is refused;
  - `lab pdf render --check` fails when a committed demo PDF is stale.
- [ ] Failure path: a corrupt, truncated or zero-page file, or one that is too big or too slow,
      gives `PdfRefused`. No partial document is stored.
- [ ] The legibility check (D10a). These bars are set before it runs:
  - **Attacks caught:** #156's 11 cases unreadable in PDFium's render are never eligible. Their
    tests pass unchanged: `UNREADABLE_IN_EVERY_RENDERER` and `UNREADABLE_IN_PDFIUM`.
  - **Stated limit:** #156's 4 `UNREADABLE_IN_ANOTHER_VIEWER` cases are listed as strict known
    failures, with a finding. Their test bodies don't change.
  - **Glyph swap:** D10's plant is `unverified`, and it comes off the known-failures list.
  - **No over-blocks:** no visible number in any round-trip document ends up `unverified`, and
    `STILL_CALLABLE` still passes. CI runs the corpus, the demo seed and the frozen cases. The
    full 7,748-document set runs once, and its result goes in the PR. If this bar fails, stop
    and report the misreads; don't loosen a setting to pass it.
  - **Each test can fail:** one planted case per test (contrast, size, together, read) fails
    only that test. A stand-in that skips the contrast floor must read `#FFFFEE` and be caught.
  - **Fail closed:** an OCR error or timeout gives `unverified`, and a missing Tesseract gives
    `ReaderUnavailable`.
  - **Refusal reason:** a number seen only as `unverified` is refused with
    `unconfirmed_digits_only`.
  - **Exploratory until confirmed:** the settings were chosen with these attacks in view. A
    fresh red-team session, on constructions it hasn't seen and with the settings frozen, is
    what confirms them. Until then the PR says exploratory.

Part B:
- [ ] Scans are detected per D11. With `DIALBACK_SCANS=off`, a scan fails closed.
- [ ] The agreement rule (D13) holds on a faked vision API:
  - agreeing digits on the matching line stay `visible`;
  - one changed digit, or a low confidence, gives `unverified`;
  - the right digits on another line give `unverified`, as does a number OCR finds twice that
    vision finds once;
  - a vision-only number is dropped;
  - an API error or an off-schema reply fails the document;
  - a number seen only as `unverified` is refused with `unconfirmed_digits_only`.
- [ ] Corpus gate B passes with C04's contract read as a scan: real Tesseract, faked vision.
- [ ] Redaction: every non-phone run of 7 or more digits that OCR reads is blacked out before
      the image is sent. The scored run reports how many runs OCR missed. No transcription is
      stored or logged.
- [ ] The scored run (D17) meets every bar on held-out seeds, or scans stay off. Either result
      is written into this spec.
- [ ] Failure path: Tesseract or the key missing with scans on stops the run at startup.

## Tests (written first)

| Item | Test |
|---|---|
| Contract | `scripts/db/test_migrate.py` and the contracts validator |
| Round trip | `evals/tests/test_pdf_roundtrip.py` |
| Hiding tricks | `packages/ledger/tests/test_pdf.py::test_<trick>_is_<class>` (one each) |
| Refusals | `packages/ledger/tests/test_pdf.py::test_refuses_<reason>` |
| Same results | `packages/ledger/tests/test_store.py` (gates through PDFs), `evals/tests/test_pdf_roundtrip.py::test_lab_docs_patterns_match` |
| Demo | `packages/ledger/tests/test_demo.py::test_pdf_load_writes_the_same_observations`, `evals/tests/test_pdf_render.py::test_check_fails_on_a_stale_pdf` |
| Known limit | `evals/tests/test_pdf_tricks.py::test_glyph_swap` |
| No overlap, and the `docgen` fix | `evals/tests/test_pdf_roundtrip.py::test_no_source_has_overlapping_spans` |
| Refusal reasons | `packages/ledger/tests/test_select.py::test_no_ink_only_is_hidden_text_only`, `::test_unverified_only_is_unconfirmed_digits_only` |
| Agreement and redaction | `packages/ledger/tests/test_scan.py` (faked API, real Tesseract) |
| Legibility check (D10a) | `packages/ledger/tests/test_pdf_review.py` (#156, unchanged), `packages/ledger/tests/test_pdf_legibility.py` |
| Scored run | `packages/ledger/scripts/scan_eval.py`, with its tests in `packages/ledger/tests/test_scan_eval.py` |

## Out of scope

- **Real-world PDF variety beyond what the renderer makes.** We have no real PDFs. The reader
  refuses what it can't account for (D4).
- **Email files and attachments.** This covers `.eml` and HTML mail. The email door
  (`sami/email-door-design`) records attachments and ignores them; reading them with this
  reader is a later spec.
- **Getting documents in:** an inbox or uploads.
- **Layout:** multi-column layout, and any reading order beyond "by baseline, then x".
- **White text on a dark background.** The white rule hides it, which is an over-block kept for
  R7.
- **Text inside images on a digital page, and handwriting.**
- **Joining a table row's cells into one line (Q159).** Today the second cell loses an email's
  `> ` quote marker.
- **Bank and routing candidates from scans.** Q44 stands.
- **Sending real account numbers in a scan to the API.** Redaction is best effort (D12), and a
  real deployment must decide this first.

## Open questions

- **Q156, resolved (Sami, Nov 11): Part A now.** That means real PDFs in the Nov 19 final's
  demo. So:
  - Part A lands after the Nov 8 freeze;
  - the core image gains pdfplumber;
  - D9 covers the team phone;
  - Part B waits until after the final.
- **Q157, open until Part B starts:** API spend for Part B's vision calls.
  - **The held-out run:** 4 profiles x 200 documents x 3 runs = 2,400 page calls.
  - **Development:** 4 profiles x 1,000 documents x however many tuning passes `C_MIN` and the
    line-match floor take.
- **Q158, resolved (Sami, Nov 11):** local OCR supplies a scan's text, and the vision call only
  confirms phone digits (D12, D13). ADR-14 is amended accordingly.
- **Q159:** should `text.py` join the cells on one baseline into one line? It would change the
  page-model path and spec 0052's results too, so it's a spec of its own.

## Commands

```bash
uv run --no-project --with pytest --with "psycopg[binary]" pytest scripts/db/test_migrate.py
cd packages/ledger && uv run pytest -q tests/test_pdf.py tests/test_scan.py && uv run pytest -q
cd evals && uv run pytest -q tests/test_pdf_roundtrip.py tests/test_pdf_tricks.py tests/test_pdf_render.py
uv run lab pdf render --check
uv run python packages/ledger/scripts/scan_eval.py --profile clean --profile office --profile fax --profile photo --held-out
```

## Calls made after the builder check (Nov 11)

These are the spec author's calls, not Sami's, and each can be overruled:

- **D3, the ink test:** compares the page with and without the span, not the box against its
  surroundings. This is stricter and slower, but it catches invisible text over other ink.
- **D6, C04:** C04 renders as a digital PDF in Part A and as a scan in Part B.
- **D6, spec 0052's renderer:** off-page rows keep their spans' relative x, a one-line change to
  `docgen.render`, with `lab docs` re-run and its changes reported.
- **D9, the team phone:** it's rendered into the PDFs at run time, outside the repo, so a stored
  hash always matches the file read.
- **The contract:** `no_ink` maps to a widened `hidden_text_only`, and `unverified` gets a new
  reason, `unconfirmed_digits_only`.
- **D12, redaction:** best effort, measured, with a real deployment's decision left out of scope.
- **D13, vision matching:** line-aligned and count-aware, not anywhere on the page.
- **D17, the scored run:**
  - zero unsafe digits on all four profiles;
  - matching to the truth by position;
  - missed numbers count as over-blocks;
  - development uses 0052 seeds 0 to 4 at 200 documents each, and the held-out set is seed
    1000;
  - a G3/G4 gate on OCR'd text.
