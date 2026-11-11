# 0041: Onboarding anchors, in shadow mode

**Owner:** Sami  **Slice:** call and adversary  **Target day:** after submission, for the Nov 19 final

## Goal

A number earns trust over time today: two source types, 90 days (spec 0021), so a new vendor's
bank change gets no call. For a vendor seen for the first time in Ramp, this spec finds a number in
sources the vendor's own onboarding entry did not supply, checks the business, and records the
number as the vendor's **anchor**. **Shadow mode:** it writes a report nothing else reads, so it
protects nothing yet and changes nothing in the pipeline. It follows R2's idea (a number the
request supplied is never trusted), R3 (code decides) and R7 (unclear means no anchor), and is a
step toward spec 0002's Q2 (a vendor's first sight).

## Contract touched

None. Nothing outside Sami's slices reads the report. A contract comes with integration.

## How it works

- **Sweep:** `dialback-onboarding sweep --dataset <id> --since <time> --out <file>` pages through
  `GET /developer/v1/vendors`, keeps vendors whose `created_at` is at or after `--since`, reads their
  contacts (`GET /developer/v1/vendors/{id}/contacts`) and sources, decides, and writes one JSON
  report. Read-only; the token is minted as the Ramp mock README documents.
- **Sources** (fixtures in `packages/onboarding/fixtures/` for now): a business-registry entry
  (legal name, state, tax ID, status, filing date, phone) and the vendor's website (domain, the
  domain's registration date, the contact page's phone). The vendor's own Ramp contacts are the
  onboarding request: shown in the report, never counted.
- **Rule:** `choose_anchor(vendor, contacts, sources, settings) -> dict`, pure, no I/O:
  1. Business checks: a registry entry exists, is active, and matches the vendor's name and state;
     the tax ID matches; the website's domain is at least `min_domain_age_days` (365) old. A
     missing or unreadable fact fails its check.
  2. A number is the anchor only if at least 2 independent source kinds show it and every check
     passes. A number from the vendor's own contacts never counts toward the 2.
  3. Two independent kinds that show different numbers: no anchor.
  4. If the anchor matches none of the vendor's own contact numbers, the report flags
     `supplied_number_unconfirmed`.
- **Output per vendor:** `anchored` with the number and source kinds, or `no_anchor` with reasons
  from a fixed list: `no_registry_entry`, `registry_inactive`, `registry_mismatch`,
  `tax_id_mismatch`, `domain_too_young`, `too_few_independent_sources`,
  `conflicting_independent_numbers`.

## Acceptance

- [ ] A vendor whose registry entry and website show the same number, with every check passing, is anchored on it.
- [ ] A number that only the vendor's own Ramp contacts show is never the anchor, whatever else holds (a property over generated inputs).
- [ ] Each reason fires on its own case and blocks the anchor; a deliberately broken rule shows each check can fail.
- [ ] Missing data, an unparseable source or a Ramp API error gives `no_anchor` or a non-zero exit, never an anchor (fail closed).
- [ ] The sweep only reads: no writes to Ramp or the database, no calls; the same inputs give the same report.
- [ ] Every number in fixtures and reports is in NPA-555-0100 to 0199; the sweep refuses to write any other.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `packages/onboarding/tests/test_anchor.py::test_agreeing_sources_anchor` |
| 2 | `packages/onboarding/tests/test_anchor.py::test_vendor_supplied_number_never_anchors` |
| 3 | `packages/onboarding/tests/test_anchor.py::test_each_reason_blocks_the_anchor` |
| 4 | `packages/onboarding/tests/test_anchor.py::test_fails_closed_on_missing_or_bad_data`, `tests/test_sweep.py::test_api_error_exits_non_zero` |
| 5 | `packages/onboarding/tests/test_sweep.py::test_sweep_only_reads`, `::test_report_is_deterministic` |
| 6 | `packages/onboarding/tests/test_sweep.py::test_refuses_real_looking_numbers` |

## Out of scope

The callback (spec 0042). Postgres storage (a new schema needs the shared `_bootstrap` folder and
`scripts/db/apply.sh`). Real registry, tax-ID and domain services. Comparing anchors with the
ledger's pick. Changing an anchor. Any change to the core, the ledger, `decide()` or a contract.
Shared files it does touch, a line each: the root `pyproject.toml` workspace list (and `uv.lock`),
the `AGENTS.md` ownership table, `CODEOWNERS`, and a test step in `.github/workflows/py.yml`.

## Open questions

- Q69: which real registry, tax-ID and domain-age services, and what they cost.
- Q70: is 365 days the right `min_domain_age_days`?
- Q71: does the purchasing employee's own record of the vendor count as an independent source kind?
- Q72: for a customer who already has vendors, which vendors does the first sweep treat as new? This is Q2's problem again.

## Commands

```bash
cd packages/onboarding && uv run pytest -q
python3 scripts/ci/boundaries.py
uv run --no-project --with ruff ruff check packages/onboarding
```
