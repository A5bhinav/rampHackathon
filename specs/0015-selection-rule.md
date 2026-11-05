# 0015: The selection rule, `ledger-select-v1`

**Owner:** Inesh  **Slice:** evidence  **Target day:** Thu Nov 5 (was Nov 3)

## Goal

`dialback_ledger.select()`: a pure, deterministic function that takes a vendor's sightings,
their documents' custody and the change request, and returns a `selection.schema.json` document
(spec 0018): a pick with up to two alternates, or `no_provable_number`, with every candidate and
its reasons. It implements the rule in the ledger README and design §4. It serves R2 (never dial
a number first seen in the change context), R3 (code decides) and R7 (anything unclear makes a
number ineligible; nothing raises into the core).

Reading `ledger.observations` and writing `ledger.number_selections`, and so `select_number`
for `DIALBACK_RESOLVER=ledger`, are the next spec (Q45). Until then, `ledger` mode keeps failing closed.

**"Seen" means visible, before the cutoff, and outside the change context.** Age, ranking and
near misses use it, so a sighting in the change context can only hurt a number. An old hijacked
thread, a lookalike's message or a line hidden in a PDF never makes a number look older or
established. A near miss is checked only against older numbers that were seen this way.

## Contract touched

None. The output is `selection.schema.json` (spec 0018).

## Acceptance

- [ ] Only rows recorded before the case opened count (replay).
- [ ] Cutoff: the earliest of the request's arrival, the first lookalike-domain message and the
      change in Ramp, all our receive times, never later than the requested cutoff.
- [ ] Change context: the request's thread; its subject line (without Re:/Fwd:) within 30 days
      before the cutoff; anything from a lookalike domain; anything received after the cutoff.
- [ ] A number is eligible only if it has a visible vendor-party sighting that isn't a fax line,
      was first seen at least 90 days before the cutoff and outside the change context, and has
      at least 2 source types before the cutoff and outside the change context
      (`vendor_master` never counts, all email counts once).
- [ ] A number one digit off, or two adjacent digits swapped, from an older vendor number is
      ineligible, unless both were first seen at least `established_days` (365) before the cutoff (Q43).
- [ ] Rank: oldest first, then more source types, then AR or billing over main, then the number itself.
- [ ] The result validates against the schema; `inputs_sha256` hashes the canonical rows used;
      the same inputs in any order give the same document.
- [ ] Unknown channels, visibility, roles or parties make a number ineligible, never eligible.
- [ ] **Corpus gate:** on all 20 frozen scenarios (P01 to P12 with P04 in three variants, K01, C01 to C05), the cutoff, outcome, pick, every listed
      rejection reason, and C05's first-seen time match the truth. 0 poisoned numbers picked except K01.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| the six properties (ledger README) and schema validity | `packages/ledger/tests/test_select_properties.py` |
| corpus gate | `packages/ledger/tests/test_select_corpus.py` |
| lookalike domains | `packages/ledger/tests/test_lookalike.py` |

## Out of scope

Database reads and writes, `select_number`, the real `change_context`, ingestion, alternates on
a dead line (Q18).

## Open questions

- **Q45 (Abhi):** the core calls `select_number` without a database connection. Should the
  `NumberResolver` port pass the pool, or should the ledger open its own from `DATABASE_URL`?
  This blocks the wiring PR.
- **Q46 (Abhi):** how does the ledger know which document is the change request? The rule needs
  its thread, subject, sender and receive time. Relates to Q37 (how the demo's request reaches the ledger).
- **Q43** is carried as the setting `established_days = 365` and is recorded in `rule_settings`
  once the contract grows to hold it. For now it is part of the rule version.

## Commands

```bash
cd packages/ledger && uv run pytest -q
```

```bash
python3 corpus/generator/build.py --check
```
