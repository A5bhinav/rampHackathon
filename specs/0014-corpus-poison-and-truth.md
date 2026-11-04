# 0014: Corpus v0: poisoning patterns, controls and frozen truth

**Owner:** Inesh  **Slice:** evidence  **Target day:** Thu Nov 5 (was Oct 31)

## Goal

A deterministic generator in `corpus/generator/` builds one fictional vendor per scenario: its
years of contracts, invoices, emails, listings and portal entries, then the poisoning or the odd
change. Code plants every phone number, so the labels are exact. For each scenario, `truth/`
freezes what was planted, where, through which custody channel, whether a person could see it,
and what `ledger-select-v1` must do. The truth is hashed and the hash is pinned, so it can't
drift to match the extractor or the rule, neither of which exists yet (design §4, ledger README).

## Contract touched

None. Documents are written in a page model (text spans with position, size and colour, the
same facts pdfplumber reports) plus custody. Rendering them to real PDFs is a later spec.

## Scenarios

Named by the design doc: **P01** one-digit-off number in a hijacked thread, **P02** lookalike
domain, **P03** slow poisoning over weeks, **P04** hidden PDF text (variants a white, b 1-point,
c off-page), **P05** backdated header, **P06** poisoned number on file.

**Proposed here, to confirm (Q42):** one per selection clause that the named six don't isolate.
**P07** same subject line, new thread, within 30 days. **P08** two swapped digits, otherwise
established (only the near-miss check catches it). **P09** injection text naming a "verified"
number (R9). **P10** documents arriving between the request and the change in Ramp. **P11** a
fake listing and emails, 60 days old. **P12** one number from several mailboxes, an attachment and
a second domain (email counts once). **K01** the stated known limit: mailbox and listing
controlled for 120 days, no stored contract; truth says the poisoned number *is* picked.

**Controls, proposed (Q42):** **C01** a real change whose thread repeats the established number
(R2 in practice). **C02** established sibling lines one digit apart. **C03** an email-only
vendor (correct refusal). **C04** the second source is a scanned contract. **C05** a re-sent
contract whose printed date is years older than its receive time.

## Acceptance

- [ ] `uv run python corpus/generator/build.py` writes every scenario byte for byte the same
      on every run; `--check` rebuilds in memory and fails on any difference with the committed files.
- [ ] 12 patterns (P01 to P12) and 5 controls (C01 to C05), plus K01.
- [ ] Each truth file lists every planted number with every sighting (document, channel, receive
      time, visibility, role, party, real or poison) and the expected cutoff, outcome, pick and
      the reasons each poisoned number must be rejected for.
- [ ] No poison scenario except K01 expects a poisoned number to be picked.
- [ ] `truth/manifest.sha256` covers every truth file, and its own digest is pinned in
      `build.py`. Changing the truth means changing that pin in review.
- [ ] Only fictional values: phones in 555-0100 to 555-0199, domains and email addresses under
      `.example`. A vendor's real numbers are never a near miss of each other, except in C02.
- [ ] An attachment has its email's custody. No full bank account number appears anywhere.
- [ ] CI runs `--check` and the tests.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| all of the above | `corpus/generator/test_corpus.py` |

## Out of scope

The 500-vendor background company (next spec). PDF rendering and the ~5% scans as images. The
demo seed. Held-out scenarios (written by a teammate, hash only).

## Open questions

- **Q42 (Abhi, Sami):** confirm P07 to P12, K01 and the five controls; the design names only six patterns.
- **Q43 (Abhi):** "unless both are long-established lines" (the near-miss clause) needs a
  number. If it meant "both older than `min_age_days` (90)", P08's swapped-digit number, planted
  150 days ago in two source types, would become an eligible alternate. The truth says it must
  be rejected, so "long-established" has to be stricter. Every scenario agrees with "both first
  seen at least 365 days before the cutoff" (C02's siblings are years old). Is 365 the rule?
- **Q44 (all):** the corpus says "account ending 4821" and never prints a full account number,
  even a fictional one, because AGENTS.md says never store one anywhere. The extractor's
  "labeled account numbers" can't be tested without them. Allow fictional ones in `corpus/` only?

## Commands

```bash
uv run python corpus/generator/build.py --check
```

```bash
uv run --no-project --with pytest pytest corpus/generator -q
```
