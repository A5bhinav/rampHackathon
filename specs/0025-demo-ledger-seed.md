# 0025: The demo's ledger: seed documents and a loader (Q36, Q37)

**Owner:** Inesh  **Partner:** Abhi  **Slice:** evidence  **Target day:** Sun Nov 8

## Goal

Give `demo reset` a Trust Ledger, so beats 1 to 4 run on the real rule instead of the stub. The
seed's three vendors (`scripts/demo/seed.json`) get years of fictional documents, built by the
corpus generator, so `ledger-select-v1` produces the outcomes spec 0011 describes:

| Ref | Ledger story | Selection | With the call |
|---|---|---|---|
| `acme` | An ordinary history; the change request replies on a real invoice thread | its AR line | the vendor confirms: Verified |
| `brightline` | A small vendor known only by email | no provable number: refused before dialing, to review | no call |
| `cobalt` | The request hijacks an invoice thread and offers a one-digit-off "AR direct" number | its real AR line; the offered number is refused | the real vendor denies: Not verified |

A loader, `dialback-ledger demo`, puts them in the current demo dataset after `demo reset`.

## Decisions

1. **Q36, the format:** the ledger's seed is its own file, `dialback_ledger/demo_seed.json`, keyed
   by the same vendor refs as `scripts/demo/seed.json`, which stays as it is. It ships inside the
   ledger package, so the core's image has it with no Dockerfile change. The corpus generator
   writes it, and `build.py --check` keeps it byte for byte (it isn't part of the frozen truth).
2. **Dates are relative:** documents are generated against a fixed anchor and shifted to the load
   time, so the history always ends just before the demo.
3. **Q37, the change-request email:** `dialback-ledger demo request --vendor <ref>` ingests that
   vendor's request email and marks it the change request. Run it right before `demo change`
   (beat 2), so the email arrives first, as in life. `demo load --with-requests` loads all three at
   once for rehearsals.
4. **The live call's number:** the fictional AR line can be swapped for a team phone at load time,
   `--phone acme=+1…`, given on the command line and never committed. The swap happens in the
   documents' text before ingestion, so the rule, the lineage and the voice service all see the
   same number. Without it, the pick stays in 555-01xx, which the voice service's allowlist refuses.
5. **Vendors are found** through `app.datasets.demo_refs` (spec 0011), the Ramp ids `demo reset`
   recorded by ref, then `app.vendors`.

## Contract touched

None.

## Acceptance

- [ ] `build.py` writes `dialback_ledger/demo_seed.json` deterministically; `--check` fails on drift;
      the frozen truth's digest doesn't move.
- [ ] After `load` and `request` for each vendor and the bank change, `select_number` picks Acme's AR
      line, refuses Brightline, and picks Cobalt's real AR line while refusing the offered number.
- [ ] `--phone ref=+1…` makes that vendor's AR line the given number everywhere the rule reads it;
      a number that isn't valid E.164 is refused before anything is written.
- [ ] A dataset with no `demo_refs`, or a ref it doesn't know, is refused before anything is written.
- [ ] Loading twice, or requesting twice, adds nothing new.
- [ ] The seed holds only fictional values (555-01xx, `.example`).

## Tests (written first)

| Item | Test |
|---|---|
| all | `packages/ledger/tests/test_demo.py` (real Postgres) and `corpus/generator/test_corpus.py` |

## Out of scope

Changing `demo reset` to call the loader itself (Abhi's CLI; a one-line follow-up if he wants it).
Switching the deployed core to `DIALBACK_RESOLVER=ledger` (Q51).

## Runbook

```bash
dialback-core demo reset --link clerk=...
```

```bash
dialback-ledger demo load --phone acme=+1... --phone cobalt=+1...
```

Then, at beat 2:

```bash
dialback-ledger demo request --vendor cobalt
```

```bash
dialback-core demo change --vendor cobalt
```

## Commands

```bash
python3 corpus/generator/build.py --check
```

```bash
cd packages/ledger && uv run pytest -q
```
