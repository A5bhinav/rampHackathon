# 0011: Demo mode and reset

**Owner:** Abhi  **Slice:** money path  **Target day:** Sun Nov 8 (built earlier, so the team can rehearse)

## Goal

The recorded demo (Nov 9) and the live final (Nov 19) walk the design's seven-beat story on the deployed stack, from a clean start, as many times as rehearsal needs:
1. the vendor risk list;
2. a bank change arrives and payments go on hold;
3. the number lineage;
4. the live call;
5. Verified, with the changer refused and a second person releasing;
6. one evidence row edited by hand, and the verify job pointing straight at it;
7. the scorecard.

A reset gives a fresh demo company in seconds and deletes nothing: every reset starts a new dataset and evidence chain (design, "Risks").

This spec covers the parts in Abhi's slice: the reset, the bank change, the tamper and the calling hours. Beats 1, 3, 4 and 7 come from Inesh's and Sami's slices; see the open questions.

## Decisions

1. **One command per beat, in the core's CLI** (`dialback-core demo …`), run in the core's Railway shell or locally with the core's environment.
   - **No dashboard buttons:** they would need an admin role, which we decided against (spec 0010, decision 8). They would also put destructive controls one click from the judges.
   - **Guards:** every command refuses a dataset that isn't kind `demo`. Every command refuses `DIALBACK_RAMP=sandbox`, because Ramp's sandbox can't be reset.

2. **`demo reset --seed <file> [--link <ref>=<email>]…`** does, in order:
   - **Retire:** every current demo dataset is hidden; nothing is deleted. Jobs and webhooks already skip retired datasets.
   - **Create:** a new demo dataset with its own evidence chain.
   - **Seed:** the dataset's Ramp mock business gets the seed's users, vendors, accounts, contacts and bills. Payment dates in the seed are relative ("+3d") and resolved against today, so the demo never shows overdue bills by accident.
   - **Webhooks:** they are subscribed to `{CORE_PUBLIC_URL}/webhooks/ramp/{dataset}` when that new, optional setting is set. Without it, the poller finds the change within its interval.
   - **Import:** the vendors, as `import-vendors` does.
   - **Re-link:** each `--link clerk=judge.clerk@…` points that Dialback account's `ramp_user_id` at the seeded user with that ref. R5 then recognizes them as the changer (spec 0010, decision 6). Emails are given at run time, never committed. Without a link, a change by that user has no known author, so it needs two approvers; the reset warns about this.
   - **Print:** the new dataset's id, its vendors and the next command.

   **As built:** every check (sandbox, link refs, emails) runs before anything is written, so a refused reset creates nothing. The new dataset is created hidden and becomes current only at the end, in the same transaction that hides the earlier demo datasets and sets the links. A reset that fails partway therefore never replaces the previous demo; its half-made dataset stays hidden (nothing is deleted).

   The dashboard's home page already opens the newest dataset, so after a reset everyone lands on the fresh demo.

3. **The demo seed** is `scripts/demo/seed.json`. It uses the mock's seed format, plus relative dates and a `changes` list: per vendor ref, the new routing and account number, fictional. It starts with the dev stack's three vendors, Acme, Brightline and Cobalt. With the dev stack's scripted calls:
   - Acme confirms and is verified;
   - Brightline's call is refused before dialing and goes to review;
   - Cobalt's contact denies the change, so it is not verified.

   On the deployed stack, the live call decides instead.

   Inesh's frozen demo seed replaces it on Nov 8 (Q36).

   **As built:** the core's image copies `scripts/demo/`, so the default `--seed` works from the Railway shell. A reset records the Ramp ids it seeded, by ref, in a new column `app.datasets.demo_refs` (migration `app/0009`). That way a later `demo change` from another shell finds the vendor and the person. Ids only: the new bank details stay in the seed file, which `demo change` reads each time (ADR-8).

4. **`demo change --vendor <ref> [--by <ref> | --unknown]`** is beat 2. It makes that vendor's seeded bank change in the mock.
   - **Author:** by default it is the linked clerk. With `--unknown` it has no known author, so two approvers are needed (ADR-10).
   - **What follows:** the mock sends its webhook, and the core opens the case and places the hold.
   - **The email:** the change-request email that the ledger reads is Q37.

5. **`demo tamper --yes-break-the-chain [--seq N]`** is beat 6.
   - **The edit:** it changes one row of the current demo dataset's chain, the way `supabase/tests/001_evidence_chain.sql` does. As the table's owner, inside one transaction, it turns the update trigger off, edits the row's `body_jcs`, and turns the trigger back on before committing. Other sessions never see the trigger off: they wait on the lock.
   - **Which row:** by default, the latest `decision_made` whose outcome isn't `verified`, rewritten to `verified`: the insider flipping a decision to get paid. `--seq N` picks another row, and is required when there is no such decision. **As built:** a row with no outcome to flip gets `"edited_by_hand":true` appended. `--dataset` refuses a retired demo.
   - **As built, after the edit:** the row is committed before the verify job runs, so nothing after it is a refusal. If the deployed worker holds the job's lease, the command prints that no alert is raised yet; the scheduled check raises it within `DIALBACK_VERIFY_SECONDS`. A dataset has one open `chain_broken` alert at a time, so a second tamper on the same demo reports the first broken row.
   - **Then:** it runs the verify job, whose one critical alert names the row.
   - **Refusals:** any dataset that isn't kind `demo`, and any run without the flag. It needs the owner's connection (`DATABASE_URL`). If the core's role doesn't own the table, the command fails and nothing changes.

6. **`demo status`** prints the current demo dataset, its cases with their states, the chain's verify result and open alerts. It is a pre-flight check before going on stage.

7. **`demo chaos on|off`** is spec 0008's deferred toggle. It sets chaos mode on the current demo business only: seeded, 300‰ errors, no dropped webhooks. It is off by default, and every reset turns it off.

8. **Calling hours.**
   - Demo datasets call within `DIALBACK_DEMO_CALLING_WINDOW`, which defaults to any time on any day. The live final is in the evening (spec 0002, Q7), and every demo number is a team phone on the voice service's allowlist.
   - **As built:** the value is `always` (the default), `HH:MM-HH:MM` (every day) or `HH:MM-HH:MM <days>`, in `DIALBACK_CALLING_TZ`. A day range may wrap the week (`fri-mon`). Anything unreadable, including a window with no open day, stops the core at startup. Both the first call and each retry timer use the dataset's window.
   - Dev and lab datasets keep the normal window.

**Stated limits:**
- **Ramp's sandbox:** with it, the demo company can't be reset, so the commands refuse to run.
- **Old demo datasets** stay in the database, hidden. A tampered one stays visibly broken; that is the point.
- **The re-link** moves a person's Ramp identity to the new business. In retired datasets their old changes no longer map to them, which doesn't matter because nobody uses retired datasets.
- **Tamper is a real edit** of the evidence table, limited to demo datasets by the command. Anyone holding the owner's `DATABASE_URL` could do the same elsewhere. That is the trade-off ADR-7 already states, and why the chain's tip is witnessed outside the database.

## Contract touched

None: the CLI and two optional core settings, `CORE_PUBLIC_URL` and `DIALBACK_DEMO_CALLING_WINDOW`. One nullable column, `app.datasets.demo_refs` (decision 3).

## Acceptance

- [x] **Reset:**
  - It creates a new demo dataset and chain, retires earlier demo datasets and deletes nothing.
  - The mock business is seeded, with relative dates resolved, and the vendors are imported.
  - The links are set. Webhooks are subscribed when `CORE_PUBLIC_URL` is set.
  - It refuses the sandbox, an unknown link ref and an email with no account.
- [x] **Change:**
  - Made as the linked clerk, it opens the case, places the hold, and the clerk can't release it (R5).
  - With `--unknown`, two approvers are needed.
- [x] **Tamper:**
  - It refuses a non-demo dataset, and refuses to run without the flag.
  - Afterwards the verify job raises one critical alert naming the edited row.
  - Other datasets' chains are untouched, and an ordinary update is still refused.
- [x] **Status** reports the dataset, its cases, the chain result and the alerts.
- [x] **Chaos** on and off changes only the demo business's mock settings, and a reset turns it off.
- [x] **Calling hours:** a demo dataset places a call at 21:00 in the calling time zone, while a dev dataset waits for the window.
- [ ] **End to end** against the dev stack: reset, change, scripted call, Verified, the changer refused, a second approver releases, tamper, the alert.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| reset | `apps/core/tests/test_demo.py::test_reset_starts_a_fresh_demo_without_deleting_anything`, `::test_reset_refuses_the_sandbox_and_bad_links` |
| change | `test_demo.py::test_the_demo_change_is_the_clerks_and_they_cant_release_it`, `::test_an_unknown_author_needs_two_approvers` |
| tamper | `test_demo.py::test_tamper_breaks_only_a_demo_chain_and_the_verify_job_names_the_row` |
| status, chaos | `test_demo.py::test_status_reports_the_demo`, `::test_chaos_toggles_only_the_demo_business` |
| calling hours | `apps/core/tests/test_worker.py::test_demo_datasets_call_outside_the_normal_window` (a demo dataset's clock is real, so the test closes the normal window for the whole of today rather than setting 21:00; it covers the first call and a retry), `::test_the_demo_calling_window_setting`, `::test_an_unreadable_demo_window_stops_the_worker_at_startup` |
| end to end | `scripts/dev/demo_rehearsal.py`, a rehearsal script against `scripts/dev/stack.py`; its output goes in the PR |

## Out of scope

- Dashboard controls for the demo.
- Loading the ledger's documents for the demo company (Inesh).
- The voice script and the live call (Sami).
- The scorecard (Sami).
- Recording the video.

## Open questions

- **Q36 (Inesh):** the frozen demo seed in `corpus/demo/`. Will it be in the mock's seed format plus `changes`, so `demo reset --seed corpus/demo/seed.json` reads it directly? Or should the reset read your format? Is Nov 8 still the date?
- **Q37 (Inesh):** how does beat 2's change-request email reach the ledger for a fresh demo dataset? Options: `demo change` hands it to your ingestion, or the ledger imports the seed's emails at reset. And what does the lineage panel need from the reset?
- **Q38 (Sami):** for beat 4, which team phone answers the live call, and is it on the allowlist for demo datasets? Does the voice service need anything at reset, such as the dataset id or a persona?
- **Q39 (team):** who runs the commands on stage, and from where: the core's Railway shell, or a laptop with the core's environment? The recorded clips stay the backup (design, "The demo").

## Commands

```bash
cd apps/core && uv run pytest
```

```bash
uv run python scripts/dev/stack.py --smoke
```

```bash
uv run python scripts/dev/demo_rehearsal.py
```
