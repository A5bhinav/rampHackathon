# packages/onboarding: onboarding anchors, in shadow mode (spec 0041)

For a vendor that is new in Ramp, this finds a phone number in sources the vendor's own onboarding
entry did not supply (a business registry entry and the vendor's website), checks the business, and
records that number as the vendor's **anchor**. It writes one JSON report and nothing else.

**Shadow mode.** Nothing reads the report, so it protects nothing yet and changes nothing in the
pipeline: no case, no hold, no call, no database, no contract. Nothing outside this folder imports it.

**Separate on purpose.** This folder is its own uv project with its own `uv.lock`. It is not a
member of the repository's uv workspace, and it touches no shared file (not the root `pyproject.toml`
or `uv.lock`, `AGENTS.md`, `CODEOWNERS` or CI). CI's existing ruff step lints it because that step
covers all of `packages/`; its tests run only by hand. To remove it, delete this folder and specs
0041 and 0042.

## Run it

```bash
cd packages/onboarding && uv run pytest -q
```

Against a running Ramp mock (reads only; the token is minted from `MOCK_RAMP_SECRET`):

```bash
cd packages/onboarding && uv run dialback-onboarding sweep --dataset <dataset id> --since 2026-11-01 --sources fixtures/sources.json --out /tmp/onboarding-report.json
```

`--ramp-url` defaults to `$RAMP_BASE_URL`; `--as-of` (when domain ages are measured) defaults to now.
Any read error, an unreadable sources file, or a number outside 555-0100 to 555-0199 exits non-zero
and writes no report.

The demo seed (`scripts/demo/seed.json`) gives its vendors no state, so against it every vendor
reports `registry_mismatch`: the registry match needs the vendor's state from Ramp (spec 0041, Q77).

## Files

| File | What it does |
|---|---|
| `src/dialback_onboarding/anchor.py` | `choose_anchor`, the rule. Pure: no I/O and no clock. |
| `src/dialback_onboarding/sweep.py` | Reads new vendors and their contacts, builds and writes the report. |
| `src/dialback_onboarding/ramp.py` | A GET-only reader for the Ramp mock's developer API. Standard library only. |
| `src/dialback_onboarding/cli.py` | `dialback-onboarding sweep`. |
| `fixtures/sources.json` | Stand-in registry and website facts for the demo seed's vendors. Fictional. |
