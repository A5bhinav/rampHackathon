# 0007: Callbacks, deadlines and the unknown changer

**Owner:** Abhi  **Slice:** money path  **Target day:** Thu Nov 5

## Goal

Every way a case waits, and every way it ends without a clean call, works and is proven end to end (design, "Commands and timers"; R1, R5, R7; ADR-10).

Most of it exists:
- **Retries:** no answer, voicemail or busy redials inside the calling window, at most 3 attempts.
- **Deadline:** three business days, then a person, with the hold still on.
- **Unknown changer:** a change with no Dialback user behind it needs two approvers.

What is missing is the **callback**. The agent's `schedule_callback(window)` tool ("call me back at 3") has nowhere to go: no contract carries it, and the `callback` timer kind does nothing. This spec closes that gap, fixes the deadline's behaviour in writing, and proves the unknown-changer release on the real services.

## Decisions

1. **A callback is a call result.**
   - The voice report says `result: "callback_requested"`, with `retry_at` set to when the vendor asked to be called (the start of their window).
   - `retry_at` is required with that result.
   - Contract change: a new value in `CallEndedBody.result`, plus that requirement. It goes in its own PR, and only adds to the contract.
2. **A callback is an attempt.**
   - It uses the same cap as no answer: 3 calls per case, counted across both.
   - With tries left the case waits in `retry_wait`; with none left it goes to a person (`needs_review`, hold on).
   - It redials the same number for the same version.
   - The callback call's answers are never combined with the redial's. As an earlier call (spec 0005, D2) they can only count against the change.
3. **Code enforces the window, not the model.**
   - The redial waits for `retry_at`, then for the vendor's calling window if `retry_at` falls outside it. That is the same rule as every retry.
   - A `retry_at` in the past redials at the next tick.
4. **The deadline is fixed when the case opens and only works while code is working.**
   - It is three business days from detection (weekends skipped).
   - A new change re-holds the case but doesn't move the deadline, so a fraudster can't buy time.
   - It fires in `held`, `calling`, `retry_wait` and `deciding`.
   - Once a person owns the case (`needs_review`, `awaiting_release`, `blocked`) or it is `releasing`, the deadline does nothing.
   - A callback set after the deadline is overtaken by it.
5. **An unknown changer needs two approvers, proven end to end.** The first approval records `release_co_approved` and leaves the case awaiting release. The second, by a different approver, releases it. Neither can be a changer or the case's reviewer (ADR-10, already in SQL).

## Contract touched

`evidence-event.schema.json` → `CallEndedBody.result` gains `callback_requested`, and `retry_at` is required with it. This goes in its own PR, and Sami's `internal.yaml` picks it up through its `$ref`.

## Acceptance

- [x] A `callback_requested` report with tries left puts the case in `retry_wait` with a timer at `retry_at`. With no tries left, the case goes to `needs_review` with the hold on.
- [x] A callback report without `retry_at` is refused (422), and the case stays `calling`.
- [x] The redial happens at `retry_at`, or at the next window opening if `retry_at` is outside the window. It is attempt n+1 and uses the same contact point. A clean redial ends Verified.
- [x] The decision engine accepts an earlier `callback_requested` call (no NR97). An earlier callback call that said "no" still decides Not verified.
- [x] The deadline survives a re-hold. It sends `retry_wait` to `needs_review`, including a case waiting on a callback past it. It doesn't touch `awaiting_release`.
- [x] The smoke run, on the real services: a vendor asks for a callback, is redialed and confirms. The change had no known changer, so the first approver's release leaves the case awaiting release, and the second approver's releases it.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| callback guards and timer | `supabase/tests/009_callbacks.sql` |
| refused without `retry_at` | `apps/core/tests/test_worker.py::test_a_callback_without_a_time_is_refused` |
| redial and verify | `test_worker.py::test_a_callback_is_redialed_at_the_time_asked_and_inside_the_window` |
| engine accepts it | `packages/decision/tests/test_rules.py::test_an_earlier_callback_call_is_valid_and_counts_against` |
| deadline | `test_worker.py::test_the_deadline_survives_a_re_hold_and_overtakes_a_callback`, `::test_the_deadline_leaves_a_case_a_person_owns_alone` |
| real services | `scripts/dev/smoke.py` |

## Out of scope

A holiday calendar; a cap on how far ahead a callback may be (the deadline caps it in practice); callbacks to a different number (never: R2).

## Open questions

- **Q23 (team):** should business days skip US federal holidays? Today only weekends are skipped.
- **Q24 (Sami):**
  - Does `schedule_callback` end the call and report `callback_requested`, with `retry_at` at the start of the window the vendor named?
  - What happens if the vendor names no time? Proposal: the agent asks once, then ends with `declined`.
- **Q25 (team):** a callback counts as one of the 3 attempts. Should an honest "call me back" have its own allowance?

## Commands

```bash
scripts/db/test.sh
```

```bash
cd apps/core && uv run pytest
```

```bash
uv run python scripts/dev/stack.py --smoke
```
