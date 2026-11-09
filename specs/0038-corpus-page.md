# 0038: The number-picking rule against the frozen corpus, on a public page

**Owner:** Abhi (the corpus and rule are Inesh's; partner review)  **Slice:** evidence (dashboard)  **Target day:** Nov 8

## Goal

The corpus holds 20 frozen cases (14 planted attacks, 1 stated known limit, 5 legitimate
controls), and the ledger's gate test runs the real rule on them, but nobody outside the repo
can see that. A public `/corpus` page shows each case's story, what the truth expects, what the
rule actually did, and why each number was refused. It is labelled as the number-picking rule
against planted attacks, not the attack lab, and the page computes nothing.

## Decisions

1. **A script produces the data:** `scripts/corpus/results.py` builds `select()`'s inputs exactly
   as `packages/ledger/tests/test_select_corpus.py` does, runs `ledger-select-v1`, applies the
   same checks as that gate, and writes `apps/web/src/data/corpus-results.json` with the
   ledger's own reason sentences. `--check` fails when the file is stale, and a ledger test
   (`test_corpus_results.py`) fails when it no longer matches the rule.
2. **The page is static and public** (`/corpus`), built from that file, linked from `/`.
   Numbers in the corpus are fictional (555-01xx).
3. **Per case:** id, title, vendor, kind; expected and actual pick, and whether they match (the
   script's verdict); every planted number with its truth (real or planted by the attacker),
   sightings, channels, first seen, and the rule's verdict with reason chips (the ledger's
   sentence on hover and in text).
4. **K01 is said plainly:** the rule picks the attacker's number there. That is the stated
   limit (a mailbox and a listing controlled for months, no stored contract), and the page
   says so instead of hiding it.

## Contract touched

None.

## Acceptance

- [ ] `/corpus` renders without sign-in and shows 20 cases grouped as attacks, the known limit
      and controls, with the script's match verdict for each.
- [ ] Each refused number shows its reasons as words; a number the attacker planted is marked.
- [ ] K01 says the rule picks the attacker's number, and why that is a stated limit.
- [ ] The page says it is the selection rule only, and that the attack lab isn't built.
- [ ] The committed results match what the rule does today (test).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 5 | `packages/ledger/tests/test_corpus_results.py` |
| 1–3 | `apps/web/tests/corpus.test.ts` (grouping, verdict words, the limit) |
| 4 | Checked by eye at 1440 and 390 |

## Out of scope

The attack lab and its scorecard; the 500-vendor background company.

## Open questions

None.

## Commands

```bash
uv run python scripts/corpus/results.py --check
cd packages/ledger && uv run pytest
cd apps/web && pnpm test && pnpm typecheck && pnpm build
```
