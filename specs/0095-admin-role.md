# 0095: the admin role

**Owner:** Abhi  **Slice:** money path  **Target day:** Nov 11

## Goal

Add a fourth role, `admin`, with an organisation admin's powers on the dashboard:

- see everyone and their roles;
- grant and take away clerk, approver, auditor and admin;
- turn a person's access off and on;
- set which Ramp user each person is;
- run the demo commands (reset, chaos, tamper) from the dashboard instead of the core's shell.

An admin also holds every power of the other three roles, so an admin can review and release. R5 still applies to an admin as to anyone: the person who entered a change can't clear, release or reject it.

Every admin action is appended to a new hash-chained organisation log (R6). Today roles change in two places, and neither records who granted what:

- the sign-up trigger, which grants clerk (spec 0027);
- `scripts/db/add_user.py`, run as the table owner.

This spec logs the script's changes too, with a system actor.

**After the freeze.** This lands after feature freeze (Nov 7) and code freeze (Nov 8). Abhi asked for it on Nov 10 for the live final on Nov 19. It is behind no flag. A person merges each PR.

**Earlier decisions this reverses (Abhi, Nov 10):**

- **An admin can release.** Design §7, spec 0010 Q34 and spec 0027 line 80 kept the admin out of releases.
  - **The cost is real.** Sign-up is open with no email confirmation (spec 0027), so one admin can make a second account, grant it approver, and act as two people. That gets around the review-then-release rule and the two-approver rule for an unknown changer. Those are the guarantees R5 and ADR-10 exist for.
  - **What this spec does about it:** R5 still holds per account. Every grant is on the organisation chain and the People page.
  - **What it does not do:** stop a dishonest admin. Q2 asks whether to require a second admin.
- **Demo buttons on the dashboard.** Spec 0011 line 23 gave two reasons against them:
  - they would need an admin role;
  - they put destructive controls one click from the judges.

  This spec adds the role. Against the second reason: judges sign in with demo accounts, which can never be admins (D6), and tamper needs a typed confirmation.

## Contract touched

In its own PR (Q1): `packages/contracts/openapi/dialback.yaml` and a new `packages/contracts/schemas/admin-event.schema.json` with three examples.

- **`Role` gains `admin`.**
- **New schemas:**
  - `PersonAdminView`, `People`;
  - `RoleChange`, `RampLinkChange`, `AccessChange`;
  - `AdminLogEntry`, `AdminLog`;
  - `DemoState`, which includes the last reset's `warnings`;
  - `DemoResetRequest`, `DemoChaosRequest`, `DemoTamperRequest`.
- **New endpoints:**
  - `GET /api/people`
  - `POST /api/people/{user_id}/roles`
  - `POST /api/people/{user_id}/ramp-link`
  - `POST /api/people/{user_id}/access`
  - `GET /api/admin/log`
  - `GET /api/admin/demo`
  - `POST /api/admin/demo/reset`
  - `POST /api/admin/demo/chaos`
  - `POST /api/admin/demo/tamper`
- **Error codes.** Refusals reuse the existing codes, so `Error.code` doesn't widen:
  - `forbidden` (403)
  - `transition_refused` (409)
  - `not_found` (404)
  - `invalid_request` (422)
- **Role lists.** `roles` in `/api/me` and in `PersonAdminView` lists the roles a person **holds**, not the ones admin implies.

## Decisions

1. **Admin counts as every role.**
   - `app.has_role(user, role)` returns true when the person holds `role` or holds `admin`, **and their access is on** (D7).
   - Every role check in the database goes through `has_role`:
     - `eval_guard`'s approver guards (0002, 0004, 0008, 0011);
     - `release_case` (0005);
     - the pending `0015_email_dismiss.sql` on `sami/email-door-design`, which was checked.
   - No RLS policy or Python code calls it. So an admin passes every role check without any of them being redefined.
   - The R5 checks (changer, reviewer, first approver) sit beside the role check and are untouched.
   - In the core, `permissions()`, `_refusal()` and `change_access()` check role names directly. They treat `admin` as holding every role.
2. **Admin powers need `admin` itself.** The doors check the `admin` row directly (`app.admin_caller()`), not through `has_role`.
3. **One door per power.** Each power is a security-definer function that reads `auth.uid()`; no server key acts as a person (ADR-9). The doors are:
   - `app.admin_people()`
   - `app.admin_set_role(user, role, grant, reason)`
   - `app.admin_set_ramp_link(user, ramp_user_id, reason)`
   - `app.admin_set_access(user, enabled, reason)`
   - `app.admin_log(limit)`
   - `app.admin_record_demo(type, detail, reason)`
   - `app.admin_caller()`

   Each door:
   - refuses nobody signed in, a non-admin, a demo account, and a caller whose access is off, all with `insufficient_privilege`;
   - refuses an unknown person with `no_data_found` (404).

   The doors that change something also:
   - need a written reason (`check_violation`);
   - refuse a no-op;
   - take one advisory lock, then check the caller *after* the lock.

   Only these doors are granted to `authenticated`. The helpers (`admin_append`, `admin_keep_one`, `admin_person`, ...) are granted to nobody.
4. **There is always an admin who can act.**
   - Any change that would leave no admin is refused. "Admin" here means holding admin, access on, and not a demo account. That covers removing admin from someone and turning off someone's access.
   - Every door that changes something takes the same advisory lock first, so two admins acting at once are serialised and the second sees the first's change.
   - `add_user.py --remove-role admin` makes the same check.
5. **Whoever links a Ramp user is a changer of that user's changes.**
   - An admin can't change their own link, and Ramp links stay unique.
   - **That alone isn't enough.** An admin could link a colleague to the admin's *own* Ramp identity, make the change in Ramp, and release it as "not the changer".
   - **The fix:** `user_profiles.ramp_linked_by` records the admin who set a link. When a change is recorded, `bank_change_events.changed_by_linker` copies the linker from the changer's profile, and a trigger on `app.cases` adds that linker to `changer_ids`. R5 then refuses the linker everywhere it refuses the changer.
   - **When the link moved:** the trigger reads the changer's link and its linker together, locked. If the changer no longer holds the Ramp user the change came from (relinked since detection looked them up), the author is recorded as unknown, so two approvers are needed (ADR-10). This fails closed.
   - **The cost:** an admin who links a clerk can't release that clerk's changes; another approver must.
   - **Team links have no linker:** links the team sets with `add_user.py` or the shell's `demo reset --link` clear it, but only when the link actually changes. Re-running with the same Ramp id keeps the linker.
6. **Demo accounts never hold admin.**
   - Granting admin to a demo account is refused.
   - A demo account acting as an admin is refused.
   - `add_user.py` refuses admin together with `--demo-only` in either order.
7. **Turning access off.**
   - Sets `user_profiles.disabled_at` and `disabled_by`, and keeps the person's roles.
   - **In the database:** `has_role` is false for a turned-off person, so they can't review or release even with a fresh Supabase token.
   - **In the core:** `person_tx` refuses them with 403 before any query, and a failed lookup refuses too (R7).
   - **On the dashboard:** a turned-off person sees "Your access is turned off. Ask an admin."
   - **Limits:** this doesn't delete the Supabase account (the core holds no Supabase admin key, spec 0034). It also doesn't stop the person signing up again with another email, which gets them only the clerk role.
8. **The organisation chain.**
   - One `audit.chains` row labelled `org`, made by the migration, with its id in a single-row `app.org_settings`.
   - Each change appends one envelope (`admin-event.schema.json`) through `audit.append_evidence`:
     - `v`, `chain_id`, `event_id`, `type`, `occurred_at`;
     - `actor`: a person, or the system for `add_user.py`;
     - `subject_user_id` (null for demo actions), `detail`, `reason`;
     - never `dataset_id` or `case_id`.
   - The types are `role_granted`, `role_revoked`, `ramp_link_set`, `access_disabled`, `access_enabled`, `demo_reset`, `demo_chaos` and `demo_tamper`. A refusal appends nothing.
   - **Limits:**
     - The chain is verified when the People page reads it (`audit.verify_chain`). Nothing checks it on a schedule, because `app.job_runs` and `app.alerts` are per dataset.
     - Like every chain, its rows are readable by anyone signed in (`audit/0001:259`), and so are `user_roles` and the new profile columns.
     - `supabase/README.md`'s "one chain per dataset" gains this exception.
9. **Demo commands over HTTP.**
   - The core first calls `app.admin_record_demo` as the signed-in admin. That is the admin check, and it records the intent on the chain *before* anything runs, so a command that fails partway is still logged.
   - Only then does the core run the existing `demo.reset`/`demo.prepare`, `demo.chaos` or `demo.tamper` with a `DemoEnv` built from its own settings, as `main.py` does.
   - **The ADR-9 exception.** These commands run with the core's own database connection, as the shell does. ADR-9 forbids that for *human decisions on a case*. A demo command decides nothing about a case or a payment:
     - it acts only on the current demo dataset;
     - `_guard` refuses it whenever `DIALBACK_RAMP=sandbox`.
   - **Reset** takes an optional `link_clerk_user_id`:
     - The link goes through `app.admin_set_ramp_link` as the admin, so the same refusals and log apply: not yourself, one person per Ramp user, linker recorded (D5). Then `demo.prepare` loads the Trust Ledger and Acme's change-request email (spec 0077).
     - **On demo day,** the admin who resets with a clerk becomes that clerk's linker, so they can't release the demo's changes. Another approver must, such as the demo approver account.
     - Without a clerk, it runs plain `demo.reset`.
     - The team phone (`--phone`) is never accepted over HTTP. A dashboard reset therefore leaves Acme's AR line fictional, and live calls are refused by the dial allowlist (fail closed) until someone runs `dialback-core demo prepare --phone` from the shell.
   - **Tamper** needs the typed confirmation `break the chain`. A wrong one fails the request schema (422).
   - **Errors:** a `DemoError` becomes 409 `transition_refused` with its sentence. Any other error becomes a 500 with nothing more run; the intent is already on the chain.
10. **The server role can't undo this.** A trigger refuses the core's own database role (`service_role`) when it tries to:
   - grant or remove admin;
   - turn access on or off;
   - set or clear a linker, except clearing one while relinking the person, as a shell reset does;
   - rewrite a change's linker.

   Only the doors (as the owner) and `add_user.py` (as the owner) change these.
11. **Names.** The dashboard shows roles through one map (Clerk, Approver, Auditor, Admin) wherever it prints them (`shell.tsx`, `settings.ts`).

## Acceptance

Database:

- [ ] A1. An admin can clear a review and release a change someone else entered.
- [ ] A2. R5 holds for an admin:
  - clearing, releasing or rejecting a change the admin entered is refused;
  - an admin who cleared a review can't also release it;
  - an unknown-changer case still needs two different people.
- [ ] A3. Each door refuses a clerk, approver, auditor, demo account, turned-off admin and nobody signed in, with `insufficient_privilege` (42501). A refusal appends nothing to the org chain.
- [ ] A4. `admin_set_role` grants and revokes each of the four roles. It refuses with `check_violation`:
  - a missing reason;
  - a no-op;
  - admin for a demo account;
  - removing the last admin who can act.

  An unknown person gets `no_data_found`.
- [ ] A5. Two admins removing each other's admin at once leave exactly one admin.
- [ ] A6. `admin_set_ramp_link` sets and clears another person's link. It refuses the caller's own link and a Ramp user linked to someone else.
- [ ] A7. `admin_set_access` turns access off and on and keeps roles. It refuses yourself. The last-admin check there is defensive only: the caller is always an admin with access on. A turned-off approver can't release (`has_role` is false).
- [ ] A8. Every successful change appends one event with actor, subject, detail and reason, and the org chain verifies.
- [ ] A9. `admin_people()` returns everyone with a profile: email, roles, Ramp link, demo-only, access and last sign-in. It returns this to admins only, and installs on plain Postgres, which has no `auth.users`.
- [ ] A10. The admin who set a person's Ramp link is in `changer_ids` for that person's changes, and can't release them.
- [ ] A10b. A new change on an unfinished case, made through a link an admin set, adds that admin as a changer. A change whose author no longer holds the Ramp user it came from is recorded with no known author.
- [ ] A10c. The server role can't grant admin, change access, or clear a linker without relinking (D10).
- [ ] A11. Only the doors are executable by `authenticated`. `anon`, `service_role` and `public` can execute none of them. Signed-in people can't write `org_settings`, `user_roles` or `user_profiles` directly.

Core:

- [ ] A12. `GET /api/me` lists `admin`. `permissions`, `_refusal` and `change_access` treat an admin as every role, and the R5 reasons still appear.
- [ ] A13. Each people and admin endpoint matches the contract. Errors map as follows:
  - `insufficient_privilege` → 403 `forbidden`;
  - `check_violation` → 409 `transition_refused`, with the database's sentence;
  - `no_data_found` → 404.
- [ ] A14. A turned-off person gets 403 on every `/api` request, including `/api/me`.
- [ ] A15. The demo endpoints:
  - Reset with a clerk links them through the door, logged as `ramp_link_set`, and logs `demo_reset`. Reset without one runs plain reset.
  - Chaos on and off each log `demo_chaos`.
  - Tamper with the confirmation breaks only the current demo dataset's chain and logs `demo_tamper`.
  - `DIALBACK_RAMP=sandbox` is refused with 409.
  - A non-admin gets 403 and nothing runs or is logged.
  - A command that fails after its intent was logged leaves the log entry and returns an error.
- [ ] A16. Two concurrent demotions through the API leave one admin (A5, with two connections).

Dashboard:

- [ ] A17. An admin's People page lists everyone. For each person it offers:
  - role switches, each needing a reason;
  - the Ramp link, except on the admin's own row;
  - access on/off.

  It also shows the activity log with the chain's verified badge. A refusal shows the database's sentence. Nothing is decided in the browser.
- [ ] A18. A non-admin's People page is unchanged.
- [ ] A19. The demo page is visible only to admins. It shows the demo status and the last reset's warnings, with Reset (pick the clerk to link), a Chaos switch, and Tamper behind a typed confirmation.
- [ ] A20. Control is visible to admins. A turned-off person sees the access-off notice.

Script:

- [ ] A21. `add_user.py`:
  - `--role admin` needs `--user-id`;
  - admin together with demo-only is refused in either order;
  - removing the last admin is refused;
  - each change it makes is logged with a system actor, and a no-op logs nothing.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| A1–A4, A6–A11 | `supabase/tests/018_admin.sql` |
| A5, A16 | `apps/core/tests/test_admin.py::test_two_admins_demoting_each_other_leave_one` (two connections) |
| A12–A15 | `apps/core/tests/test_admin.py` |
| A17–A20 | `apps/web/tests/people.test.ts`, `apps/web/tests/apps.test.ts` |
| A21 | `scripts/db/test_add_user.py` |

## Pull requests

Each was meant to be under about 400 changed lines, not counting generated code. Two aren't: the contract is about 700 lines (the OpenAPI and the envelope schema) and the SQL about 900 (migrations and test). Both are kept whole on purpose: splitting either would leave a door without its tests, or an endpoint without its schema.

1. This spec.
2. The contract.
3. The migrations (`app/0018_admin_role.sql`, `app/0019_admin_powers.sql`), the SQL test and `add_user.py`.
4. The core.
5. The dashboard.

Migrations are numbered 0018/0019 to stay clear of `0013`–`0017` on Sami's open branches. The runner applies by name and accepts files that land later. Those branches redefine `eval_guard` and `release_case` but call `has_role`, so D1 and D7 reach them.

## Out of scope

- Deleting Supabase accounts, invitations, password resets.
- Per-dataset roles.
- Editing rules, the call script or calling hours on the dashboard.
- Restricting who can read the org chain (D8).
- A scheduled check of the org chain.

## Open questions

- **Q1.** `Role` appears in responses, so adding `admin` widens a response enum. The contracts README allows growth only. Do Inesh and Sami agree it counts as growth? The dashboard only checks `roles.includes(...)` (`lib/apps.ts:50`, `components/shell.tsx:70`); nothing switches over every role.
- **Q2.** Should granting approver or admin need a second admin's confirmation? Without one, an admin with a second account can act as two people (see Goal).
- **Q3.** Spec 0027 Q54 is still open: anyone signed in can read every case. A turned-off person can't act (D7) but may still read through Supabase's REST API, if the hosted project exposes the `app` schema. That isn't recorded in the repo; Sami owns the project.
- **Q4.** If an approver gives the first of two approvals on an unknown-changer case and is then turned off, their approval still counts. Should turning someone off void their pending first approvals?

## Commands

```bash
scripts/db/test.sh
uv run --no-project --with pytest --with "psycopg[binary]" pytest scripts/db/test_migrate.py scripts/db/test_add_user.py
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
cd apps/core && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
uv run --with ruff ruff check .
python3 scripts/ci/boundaries.py
```
