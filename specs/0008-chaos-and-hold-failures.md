# 0008: Chaos mode, the hold-failure paths and the held-out scenarios

**Owner:** Abhi  **Slice:** money path  **Target day:** Fri Nov 6

## Goal

The money path survives an unreliable Ramp, and fails closed whenever it can't finish (R1, R7, invariant I2). Ramp can fail in these ways:
- it errors;
- it answers late;
- it drops webhooks;
- it loses a response after doing the work;
- it refuses a release;
- it claims a release it didn't do.

Today the mock fails one hold, or every hold, on command. This spec adds a seeded chaos mode for the whole Ramp API, three release-side failure switches, and a test that runs the money path under chaos and checks the invariants.

It also fixes a gap. When the hold was lifted but the saved payment dates couldn't be restored, the release command retried and then sent the case to `needs_review` with the vendor unheld, until reconciliation noticed up to five minutes later.

It also writes Abhi's held-out lab scenarios (design, "Held-out set").

## Decisions

1. **Chaos mode in the mock is seeded and covers Ramp's API only.** A business's settings take four values:
   - `chaos_seed` (null turns chaos off);
   - `chaos_error_permille`: the chance a request answers 500, 502 or 503;
   - `chaos_max_latency_ms`: a random delay before answering;
   - `chaos_drop_permille`: the chance a webhook delivery is dropped, never sent, so the poller has to find the change.

   Half of the injected errors happen **after** the request did its work and committed it. That is the lost-response case, so every call must be safe to retry. The mock's own `/_mock` routes and the payment run are never affected. The same seed gives the same faults for the same sequence of requests, in every run: the draw depends only on the seed and the request's number, never on a database id.
2. **The release side can fail on command:**
   - `fail_releases`: the release endpoint is down;
   - `ignore_releases`: Ramp answers 200 but the hold stays;
   - `fail_bill_updates`: payment dates can't be restored.

   Each stays on until it is turned off, like `fail_holds`.
3. **A release that can't finish puts the hold back** (R1, R7). Any failure between asking Ramp to release and recording `hold_release_confirmed` puts the hold back before the command retries or gives up. The failure can be in Ramp or in the database. Placing a hold that is already on changes nothing, so the worker doesn't need to know how far Ramp got. The cases:
   - the release call fails, including when Ramp did the release and its answer was lost;
   - the read-back errors, or still shows the hold;
   - restoring the payment dates fails;
   - recording the release fails.

   When the retries run out, the case goes to `needs_review` with a critical `command_stuck` alert, and the hold is on. When a new bank change arrives mid-release (the case is no longer `releasing`), the worker puts the hold back and always raises a critical `vendor_not_held` alert. The alert also says when Ramp refused to take the hold back.

   Putting it back is best effort. When Ramp refuses that too, the reconcile job places the hold once the case is out of `releasing` (invariant I2).

   **Stated limits:**
   - A worker that crashes mid-release leaves the vendor unheld while the case is `releasing`, which is the approved state, until the lease runs out and the next try finishes the release or puts the hold back.
   - If the database commits `hold_release_confirmed` but its answer is lost, the worker puts back a hold on a released case. That fails closed: the bills stay unpaid until a person lifts the hold (Q29).
4. **The invariants under chaos,** checked by a test over six runs: three seeds at a 30% error rate, and three at 70%, where some releases give up after every try. Chaos is on while changes arrive, calls report and approvers release. Then Ramp recovers, the jobs run and the rest finish.
   - **While chaos is still on, before any reconciliation:**
     - a hold the worker confirmed stays on in Ramp until its case is released, unless Ramp refused to take it back;
     - a payment run after every round pays no vendor in that set (I2 and R1).
   - **After recovery:**
     - **R1:** a payment run pays no bill of a vendor whose case is unfinished.
     - **I2:** every unfinished case's vendor is held in Ramp.
     - No case was released without an approver's `release_approved`.
     - The evidence chain is intact.
     - Releases ran under chaos, and at least one case reached `released`.

   The 70% runs are the ones that left a confirmed hold missing before decision 3 covered every failure.
5. **The held-out scenarios never enter the repo** (design, "The held-out scenarios are not in the repo").
   - Abhi wrote them in Sami's `dialback.scenario/v1` format, which is still on `sami/contract-lab-formats`. They are written from attack-class descriptions only and checked against his schema. They carry no `expected` block: the oracle writes that.
   - Their SHA-256 is recorded below. The file goes to the CI secret or private bucket that nightly and tag runs load.

## Contract touched

None. The mock's settings are internal to `apps/ramp-mock`, and the scenario format is Sami's, used as is.

## Acceptance

- [x] **Chaos is deterministic and scoped:**
  - with chaos off nothing changes;
  - one seed gives the same faults twice;
  - `/_mock` routes are never affected;
  - an error after the work leaves the work done.
- [x] A dropped webhook is never sent, and the poller still finds the change.
- [x] One failed release is retried and the case is released.
- [x] A release endpoint that stays down, and a release Ramp ignores, each end in `needs_review` with the hold on, a critical alert, and nothing paid.
- [x] When dates can't be restored, the hold goes back on after every try, and the case ends in `needs_review` held, with a critical alert and nothing paid.
- [x] When Ramp lost the release's answer, or the read-back errors, or recording the release fails, the hold is back on before the retry, and the retry releases.
- [x] A release that races a new change raises the critical alert and is dropped as stale, even when Ramp refuses to take the hold back. The new version's hold goes on once Ramp recovers.
- [x] Under chaos (six runs), the invariants in decision 4 hold, both while Ramp is failing and after it recovers. The same seed gives the same run every time.
- [x] The held-out scenarios validate against `scenario.schema.json`, live outside the repo, and their hash is recorded here.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| chaos scoped and deterministic | `apps/ramp-mock/tests/test_mock_api.py::test_chaos_is_seeded_scoped_and_can_lose_a_response` |
| dropped webhook | `test_mock_api.py::test_chaos_drops_webhooks`, `apps/core/tests/test_chaos.py` |
| release fails once | `apps/core/tests/test_chaos.py::test_a_release_that_fails_once_is_retried` |
| release down, release ignored | `test_chaos.py::test_a_release_that_cant_be_done_or_isnt_done_leaves_the_hold_on` |
| dates can't be restored | `test_chaos.py::test_when_dates_cant_be_restored_the_hold_goes_back_on` |
| lost answer, read-back error, database error | `test_chaos.py::test_a_release_that_fails_partway_puts_the_hold_back_before_retrying` |
| race when Ramp won't take the hold back | `test_chaos.py::test_a_release_that_races_a_change_alerts_even_when_ramp_wont_take_the_hold_back` |
| invariants under chaos | `test_chaos.py::test_the_money_path_holds_its_invariants_under_chaos` |
| held-out | `scripts/lab/check_heldout.py`, which validates the file outside the repo, resolves schemas offline, and prints the hash. It rejects: an `expected` block; a non-555-01xx number, overrides included; a duplicate family or variant; a variant whose overrides make its facts invalid; and a variant that changes nothing. Its rules are tested in `scripts/lab/test_check_heldout.py`. |

## Held-out set (sealed)

- File: `heldout-abhi-v1.jsonl`, kept outside the repo. 13 families and 18 variants: 8 attacks, 5 legitimate-but-odd changes.
- SHA-256: `dfef2c854d69bc123c753d1dd545961859a5c29910ee119a1413e06716fa4064`. Review fix: two variants had no overrides, so they ran the same as their base. Each now has its own persona, which changed the hash from the first draft's `1410eed4…`.
- Authors: Abhi, drafted with Claude, from class descriptions only. None of the voice agent's prompts, the oracle's rules or the dev scenarios was read.
- Check: `uv run --with jsonschema python scripts/lab/check_heldout.py heldout-abhi-v1.jsonl --schema <scenario.schema.json>` prints `13 families, 18 variants, 0 problems` and the hash above.

## Out of scope

Chaos against the voice service (Sami); a chaos run in CI on every PR (the soak test runs three seeds); the demo's chaos toggle (Nov 8).

## Open questions

- **Q26 (Sami):** where should the sealed hash live once you add the lab: `evals/heldout/` with a manifest, or the scorecard?
- **Q27 (Sami):** do the classes I used match your README's list? `hijacked_thread_new_number`, `lookalike_domain`, `insider_changer`, `planted_listing`, `forged_documents`, `inbound_impersonation`, `line_takeover`, `legit_bank_switch` and `legit_odd_contact`. If not, rename them before the final run.
- **Q29 (team):** a hold put back on a case that is in fact `released` (the lost-commit limit in decision 3) blocks the vendor until a person lifts it. Should the reconcile job also flag a Dialback hold on a vendor whose case is released? The case is rare and fails closed, so this spec leaves it out.

## Commands

```bash
cd apps/ramp-mock && uv run pytest
```

```bash
cd apps/core && uv run pytest
```

```bash
uv run python scripts/dev/stack.py --smoke
```

```bash
uv run --with jsonschema --with pytest pytest scripts/lab/test_check_heldout.py
```
