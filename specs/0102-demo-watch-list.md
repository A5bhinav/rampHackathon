# 0102: A demo watch list, so a listed vendor's bank change is caught in seconds

**Owner:** Sami  **Slice:** the sandbox layer (demo setting)  **Target day:** before the Nov 19 final  **Builds on:** 0093, 0097, 0100

## Goal

On Ramp's sandbox a bank change waiting for review writes no event and moves no timestamp, so only
a full sweep finds it, up to about two minutes later on 430 vendors (spec 0100). That is too slow on
stage. When this is done, a sandbox server can name the vendors a demo uses, and every poll reads
those vendors directly, so with a five-second poll their changes are caught in seconds.

This is a demo setting. A real business can't know which vendor will be targeted. The core and the
mock don't change.

## Contract touched

None.

## Decisions

- **D1. The setting.** `RAMP_SANDBOX_WATCH_VENDORS`: Ramp vendor ids, separated by commas or spaces.
  Empty by default, which changes nothing.
- **D2. Where.** `list_vendors_updated_since`, with a `since`, also returns each watched vendor the
  list hasn't already got, read by id, so the core's poll syncs it. A full read (no `since`) already
  has every vendor. A watched id Ramp doesn't know is skipped with a warning, so a typo can't stop
  the poll, and can be seen in the log.
- **D3. Running it.** With a watch list, set `DIALBACK_POLL_SECONDS=5` and leave
  `DIALBACK_FULL_SWEEP_SECONDS` at its daily default: jobs run one after another, so a sweep would
  hold the poll up. The cost, accepted for a demo only: a pending change on a vendor not on the
  list could take up to a day to be found. `scripts/dev/stack.py --sandbox` applies both when the
  list is set. The first poll and sweep after a start read every vendor, so the first minute or
  two is slow. `--sandbox` also seeds a third approver, since a needs-review case with no known
  author takes three people, and prints the dashboard's command before the slow vendor import.

## Acceptance

- [x] The setting parses commas and spaces, and defaults to empty.
- [x] With a `since`, a watched vendor is listed though nothing says it changed; once, if it is
      already listed; a full read adds no extra read; with no watch list nothing extra is read.
- [x] A watched id Ramp doesn't know is skipped, with a warning naming it.
- [x] The core's poll opens a case for a pending change on a watched vendor, and doesn't without
      the watch list.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-3 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_watch_*` |
| 4 | `apps/core/tests/test_pending_bank_change.py::test_the_poll_*` |

## Commands

```bash
cd apps/core && uv run pytest -q tests/test_ramp_sandbox_shapes.py tests/test_pending_bank_change.py
```
