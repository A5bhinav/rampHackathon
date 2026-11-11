# 0092: Real PDFs and scans: render them, read them back

**Owner:** Sami (the reader is in `packages/ledger`, the renderer in `evals`)  **Slice:** evidence  **Target day:** not set (Q156)

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
  input. Part B amends ADR-14 (Q158): a scan's text comes from local OCR, and the vision call
  only confirms its phone digits.
- **The visibility rule:** hidden values are kept, marked, and never eligible (`select.py` counts
  only `visible` sightings).

## Contract touched

`supabase/migrations/ledger/`: the enum `ledger.visibility` grows two values. They land in their
own PR before Part A's code (contracts only grow):

- `no_ink`: a person can't see it, because the rendered page shows nothing where it sits
  (Part A).
- `unverified`: its digits couldn't be confirmed (Part B).

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
   - `no_ink`: the page, rendered with PDFium (`pypdfium2`, which pdfplumber already depends
     on), shows no ink in the character's box. Ink means pixels that differ from the box's
     surrounding background. Whitespace is exempt.

   The ink rule catches three things:
   - text under a filled shape or an image drawn after it;
   - text in invisible render mode (mode 3);
   - text clipped away.

   The resolution and contrast threshold are the builder's, pinned by two tests: every visible
   character in every round-trip document has ink, and every planted hidden one has none.
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
   - **`dialback-ledger demo load --pdf`:** loads those PDFs instead of the JSON. It writes the
     same observations as `demo load`, apart from `sha256` and `extractor_version`.
   - **`dialback-ledger read-pdf FILE`:** prints each line with its visibility, plus the phone
     candidates code found. It labels and stores nothing.
10. **Known limit until Part B: the pixels say one thing, the text layer another.** In two cases
    there is ink, but not the digits the text layer holds:
    - a font whose character map is altered, so it shows one digit and extracts another;
    - an image drawn over text, showing other digits.

    Part A reads the text layer. So the lab plants one of each, using fontTools on a font whose
    license allows changes, and lists both as strict known failures, in spec 0048 D3's manner,
    with their findings. They turn red once Part B's pixel check lands.

### Part B: scans and the pixel check

11. **What a scan is:**
    - a page with no characters and one image covering at least 90% of it;
    - a PNG, JPEG or TIFF file;
    - a page whose only text is in invisible render mode over a full-page image. That's someone
      else's OCR, and it's ignored.
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
      - Where its output goes: its transcription never reaches the labeler and is never stored.
13. **The agreement rule.**
    - **Visible:** a phone candidate that code finds in the OCR page model stays `visible` only
      if both hold:
      - every OCR word holding its digits has a confidence of at least `C_MIN`;
      - code finds the same E.164 number in the vision transcription of the same page.
    - **Otherwise unverified:** its span becomes `unverified`. It's kept, it's never eligible,
      and it has no quote.
    - **Vision only:** a number that only the vision call reads is not a candidate. It's counted
      as dropped, with the reason "vision only".
    - **`C_MIN`:** set on development data, then frozen before the held-out run.
14. **The pixel check on digital PDFs.**
    - **The check:** every visible phone candidate in a digital PDF is also read by local OCR,
      from the rendered page region around it.
    - **The result:** if OCR's digits differ from the text layer's, or its confidence is below
      `C_MIN`, the span is `unverified`.
    - **What it closes:** decision 10's two cases. It makes no API call.
15. **Flags and installs.**
    - **`DIALBACK_SCANS=off|on`:** the default is `off`. With it off, a scan page fails its
      document closed with `PdfRefused("scan")`. With it on, both Tesseract and
      `ANTHROPIC_API_KEY` are needed, or the run stops at startup with `ReaderUnavailable`.
    - **The pixel check:** always on once Part B lands. It needs Tesseract, so the core image
      and CI install `tesseract-ocr`.
16. **The lab's scans.** `pdfrender` rasterizes each rendered page with PDFium, then applies one
    of four damage profiles. Each is deterministic by seed:
    - `clean`: 300 dpi;
    - `office`: 200 dpi, rotation up to 1°, mild noise, and JPEG;
    - `fax`: 204 by 98 dpi, black and white, with speckle;
    - `photo`: perspective, blur and uneven light.

    The truth is the plan's, so every digit can be scored.
17. **The bars, set now, for Part B's scored run.**
    - **The run:** held-out seeds 1000 to 1199, never run before (development uses 0 to 99), on
      each profile, 3 times, because the vision call varies.
    - **No unsafe digits:** in every run, no `visible` phone number whose digits differ from the
      truth. Zero of N only bounds the true rate below about 3/N (the rule of three), so the
      report states N.
    - **Over-blocks:** a true visible number left `unverified` is an over-block. They must be at
      most 2% on `clean` and at most 10% on `office`, in every run. `fax` and `photo` are
      reported only.
    - **The pixel check:** on Part A's round-trip documents it leaves no visible number
      `unverified`, and it catches both decision-10 plants.
    - **Reported, exploratory:** OCR alone and vision alone, per profile.

    If a bar fails, scans stay off. Changing a bar or `C_MIN` after the held-out run needs fresh
    held-out seeds, and the first result is reported next to the new one.

## Acceptance

Part A:
- [ ] The contract PR adds `no_ink` and `unverified` to `ledger.visibility`. The migration
      tests pass, and nothing else in the schema changes.
- [ ] Round trip (D7) is exact for every corpus document, every demo seed document and every
      spec 0052 case and seed listed. Both stand-ins fail it.
- [ ] Each hiding trick is classed correctly, with one planted test each:
  - white and near-white (`#f4f4f4`), in gray, RGB and CMYK;
  - an unconvertible colour;
  - tiny;
  - off the page past each edge;
  - covered by a rectangle, and covered by an image;
  - invisible render mode;
  - clipped;
  - a hidden digit inside a visible number, which gives no candidate.
- [ ] Every refusal reason in D4 has a test, and none stores anything.
- [ ] Corpus gates A and B pass through the PDF path, and `lab docs` at seed 0 gives the same
      patterns through it.
- [ ] `demo load --pdf` writes the same observations as `demo load` (D9), and
      `lab pdf render --check` fails when a demo PDF is stale.
- [ ] Failure path: a corrupt, truncated or zero-page file, or one that is too big or too slow,
      gives `PdfRefused`. No partial document is stored.
- [ ] D10's two plants are listed as strict known failures, with their findings in
      `evals/FINDINGS.md`.

Part B:
- [ ] Scans are detected per D11. With `DIALBACK_SCANS=off`, a scan fails closed.
- [ ] The agreement rule (D13) holds on a faked vision API:
  - agreeing digits stay `visible`;
  - one changed digit, or a low confidence, gives `unverified`;
  - a vision-only number is dropped;
  - an API error or an off-schema reply fails the document.
- [ ] The image sent to the API has every non-phone run of 7 or more digits blacked out, and no
      transcription is stored or logged.
- [ ] The pixel check (D14) marks both D10 plants `unverified`, and they come off the
      known-failures list.
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
| Known limits | `evals/tests/test_pdf_tricks.py::test_glyph_swap`, `::test_image_over_text` |
| Agreement and redaction | `packages/ledger/tests/test_scan.py` (faked API, real Tesseract) |
| Pixel check | `packages/ledger/tests/test_scan.py::test_pixel_check_*` |
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

## Open questions

- **Q156:** the target day. Is this meant for the Nov 19 live final (code freeze was Nov 8)?
  Part A alone is enough for real PDFs in the demo.
- **Q157:** API spend for Part B's vision calls. That covers development, plus 4 profiles x 200
  held-out pages x 3 runs.
- **Q158:** amending ADR-14 so that a scan's text comes from local OCR and the vision call only
  confirms phone digits (D12, D13). The two-reading rule is from the Nov 11 conversation. Which
  reading becomes the text layer is this spec's call.
- **Q159:** should `text.py` join the cells on one baseline into one line? It would change the
  page-model path and spec 0052's results too, so it's a spec of its own.

## Commands

```bash
uv run --no-project --with pytest --with "psycopg[binary]" pytest scripts/db/test_migrate.py
cd packages/ledger && uv run pytest -q tests/test_pdf.py tests/test_scan.py && uv run pytest -q
cd evals && uv run pytest -q tests/test_pdf_roundtrip.py tests/test_pdf_tricks.py tests/test_pdf_render.py
uv run lab pdf render --check
uv run python packages/ledger/scripts/scan_eval.py --profile clean --profile office --held-out
```
