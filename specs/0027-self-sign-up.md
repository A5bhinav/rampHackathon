# 0027: Self sign-up

**Owner:** Abhi  **Slice:** money path (dashboard shell and the `app` schema)  **Target day:** Sun Nov 8

## Goal

Anyone with the dashboard's address can create their own account with a name, an email and a
password, and is signed in straight away. Every new account is an employee account: it gets a
profile and the clerk role. The approver role, which is what releases money, is still granted
by the team (`scripts/db/add_user.py`, by the account's user id), so self sign-up never lets
someone release a payment.
R5 is unchanged: nobody releases their own change, whatever their roles.

This replaces spec 0010, decision 6 ("accounts are made by the team; nobody signs up"). The
team decided on Nov 8 that everyone makes their own account and access levels are granted on
top. The rest of 0010 stands: password sign-in, the 12-hour cap, the core verifying every token.

## Decisions

1. **Supabase settings (done on Nov 8 through the management API):** sign-ups on, "Confirm
   email" off (`mailer_autoconfirm = true`), minimum password length 8 (the team chose 8). Supabase's built-in mail reaches only members of
   our Supabase organization, so a confirmation link would never reach the judges.
2. **The database welcomes each new account.** An `after insert` trigger on `auth.users` calls
   `app.welcome_new_user(id, email, name)`, which adds the profile (the name the person gave,
   or the part of the email before the `@`) and the clerk role, and changes nothing that is
   already there. Accounts that exist with no role when the migration runs get the same.
   The trigger exists only where `auth.users` does (Supabase); the installer is a function so
   the SQL test can run it against a stand-in table. It reads the row as JSON, so a stand-in
   without Supabase's columns works, and it skips anonymous sign-ins. Control characters in a
   name become spaces, and names are cut to 80 characters, because someone calling Supabase
   directly skips the form.
3. **The dashboard never grants anything.** It calls `signUp` with the publishable key, as
   sign-in does; the role comes from the trigger, running as the table owner. Neither
   function can be called by `anon` or `authenticated`.
4. **The form:** name (1 to 80 characters), email, password (8 to 72 characters; 72 is
   Supabase's limit). Its own budget of 10 tries per client per 5 minutes, apart from sign-in's.
   The form is a convenience: Supabase's settings are the control. The answers:
   - an email that already has an account: "An account with that email already exists. Sign in
     instead." This tells someone who has an account; accepted for the hackathon.
   - a password Supabase calls weak, or one outside the length: say so.
   - a rate limit: wait. Supabase down: "not available". Anything else: a generic failure.
   - an account created without a session (confirmation still on): say the account needs to be
     confirmed by the team, and sign nobody in (fail closed).
5. **The sign-in page links to sign-up, and back.** Both only in Supabase mode.
6. **Approver and auditor are granted by user id.** With no email confirmation, anyone can sign
   up with someone else's address first, so `add_user.py` refuses those roles without
   `--user-id`, and refuses an id that isn't the email's account. The dashboard's header shows
   the signed-in person's id, so reading it shows they hold the password. Clerk, and removing
   a role, need only the email.

## Contract touched

None. `GET /api/me` already returns the roles.

## Acceptance

- [ ] A new `auth.users` row gets a profile with its given name, or its email's local part, and the clerk role.
- [ ] A user who already has a profile or roles keeps them unchanged; nobody gets approver or auditor from sign-up.
- [ ] Existing accounts with no role become clerks when the migration runs; accounts with a role are untouched.
- [ ] `anon` and `authenticated` can't call the welcome or installer functions.
- [ ] On a database without `auth.users`, the migration applies and installs no trigger.
- [ ] The sign-up form checks the name, email and password before reaching Supabase.
- [ ] Each Supabase answer maps to one notice (exists, weak, slow, unavailable, failed, needs confirming); only a returned session signs the person in.
- [ ] `add_user.py` refuses approver and auditor without the account's own `--user-id`, and grants nothing when it refuses.
- [ ] A name with control characters or over 80 characters is cleaned in the database; an anonymous sign-in gets nothing.
- [ ] End to end on the hosted project: sign up on the deployed dashboard, land signed in, `/api/me` names the person with `["clerk"]`.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| welcome, unchanged roles, backfill, no grants, no trigger without auth.users | `supabase/tests/013_self_sign_up.sql` |
| form checks, answer mapping | `apps/web/tests/auth.test.ts` (`cleanName`, `cleanNewPassword`, `signUpNotice`) |
| grants by user id | `scripts/db/test_add_user.py::test_approver_and_auditor_need_the_accounts_own_id` |
| names cleaned, anonymous skipped | `supabase/tests/013_self_sign_up.sql` |
| end to end | by hand on the deployed dashboard; the result goes in the PR |

## Out of scope

- An admin page for granting approver (0010, Q34 still applies: `add_user.py`).
- Password reset (needs email, which we don't send).
- Hiding whether an email has an account (decision 4).
- Narrowing what a signed-in account can read. Today every signed-in account reads every case
  (the `app` read policies are `using (true)`); roles gate actions only. 0010 says "no role, no
  access", which isn't what the database does. See Q54.

## Stated limits

- **Who has an account shows.** "Already exists" tells anyone that an email has an account.
- **Names aren't unique.** Anyone can sign up as "Riley Approver". The header shows each
  person's id; the case timeline shows names only.
- **The backfill.** When the migration runs, every account with no role becomes a clerk,
  including one whose roles the team removed on purpose. Clerk gates nothing today.
- **Reviews.** Today any signed-in account can clear or block a review (the guards check a
  reason and not-the-changer, no role). With open sign-up that means anyone; spec 0028 makes it
  approver-only, in its own pull request.

## Open questions

- **Q54 (Abhi):** should reading cases need a role? With open sign-up, anyone who signs up
  reads every case of every dataset. Fine for mock data; a production item otherwise.
- **Q55 (Sami):** the project's access tokens last 3600 s, but 0010 (decision 8) relies on 15
  minutes for sign-out and deletion to take effect. Set the JWT expiry to 900 s, or amend 0010.

## Commands

```bash
scripts/db/test.sh
uv run --with pytest --with "psycopg[binary]" pytest scripts/db/test_add_user.py
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```
