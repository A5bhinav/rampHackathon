# corpus: the synthetic company, poison cases and frozen truth

**Owner: Inesh. Partner reviewer: Abhi.**

Everything the Trust Ledger is tested against lives here, and all of it is fictional. Code
plants every value, so the labels are exact and the evals can be scored without a human.

## What it contains

- **A generator** that builds 500 vendors, 40 to 60 of them with years of invoices, contracts, emails and listing snapshots. Phone numbers come only from the reserved 555-0100 to 555-0199 range; domains sit under `.example`. Each vendor's real numbers are kept several digits apart so the near-miss check doesn't fire on noise. About 5% of pages are scans, to exercise the vision path.
- **Twelve poisoning patterns and five legitimate-but-odd controls**, with their expected outcomes frozen in git, hashed, before any extractor code exists. The patterns include a one-digit-off number in a hijacked thread, a lookalike domain, slow poisoning over weeks, hidden PDF text (white, 1-point, off-page), a backdated header, and a poisoned "number on file".
- **Frozen truth:** for every planted value, where it was planted, through which custody channel, visible or hidden, and whether the selection rule must accept or refuse it.
- **The demo seed** (v0 on Nov 1, frozen Nov 8): the handful of vendors the recorded demo and the live final walk through.

What is *not* here: the held-out scenarios. A teammate who didn't write the code under test
writes 10 to 20 of them from class descriptions alone, using a different model family; only
their hash is committed until the final run. They live in a CI secret or a private bucket.

## Suggested layout

```
corpus/
  generator/      the code that builds the company; deterministic from a seed
  company/        generated vendors, documents and emails (or a script that regenerates them)
  poison/         one folder per poisoning pattern, with variants
  controls/       the legitimate-but-odd changes
  truth/          planted values and expected outcomes, with a SHA-256 manifest
  demo/           the demo seed
```

## Rules

- No real-looking personal data, ever. The `boundaries` CI job fails on it. Names, addresses, tax IDs, phone numbers and domains are all generated.
- Expected outcomes are frozen and hashed before extractor code is written, and the hash is checked in CI so the truth can't drift to match the code.
- Every file the ledger ingests gets a custody record (channel, receive time, sender, thread) in the fixture, because provenance comes from custody metadata, never from a model.

## What's here now (spec 0014)

`generator/` builds 12 poisoning patterns (P01 to P12; P04 has three variants), 5 controls
(C01 to C05) and the stated known limit (K01), one fictional vendor each, into `poison/` and
`controls/`, with one truth file per scenario in `truth/`. P07 to P12, K01 and the controls are
proposed in the spec and await the team's confirmation (Q42).

```bash
uv run python corpus/generator/build.py            # rebuild (byte for byte)
uv run python corpus/generator/build.py --check    # CI: committed files match, truth unchanged
uv run --no-project --with pytest pytest corpus/generator -q
```

A truth file lists every planted number with every sighting (document, channel, our receive
time, visibility, role, party, real or poison) and what `ledger-select-v1` must do: the cutoff,
the outcome, the pick, the reasons each refused number must at least be rejected for, and any
numbers that must stay eligible. The manifest covers the truth and every documents file (an
expectation rests on their custody); its digest is pinned as `FROZEN_TRUTH_SHA256` in
`build.py`, so moving the truth or a document means changing that line in review. Documents are a page model (spans with position, size and colour) plus custody;
rendering them to PDF, and the 500-vendor background company, come next.

## Start here

1. `generator/` with a seedable builder and a `make corpus` or `uv run` entry point; commit either the output or the exact command that regenerates it byte for byte.
2. Write the 12 patterns and 5 controls as fixtures with their `truth/` entries, hash the manifest, commit (Oct 31 deliverable).
3. Hand the demo seed v0 to Abhi on Nov 1 for the walking skeleton.
