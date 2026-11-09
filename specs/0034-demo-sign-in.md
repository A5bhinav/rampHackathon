# 0034: One-click demo accounts that act only on demo data

**Owner:** Abhi  **Slice:** money path  **Target day:** Nov 8  **Builds on:** 0033, 0010, 0027

## Goal

Judges should explore the demo without making accounts: one click signs them in as the demo
clerk (who entered the bank changes, so R5 refuses them the release) or the demo approver. Those
accounts must never move anything on a sandbox or real dataset. The database enforces that, not
the page.

## Decisions

1. **A demo account is a row in `app.demo_accounts`** (migration `app/0012`), added by the team
   as the owner with `scripts/db/add_user.py --demo-only`. Nobody signed in can read or write the
   table; `app.is_demo_account()` answers for the policies and triggers.
2. **Postgres refuses a demo account outside demo data.** A trigger on `app.cases` (every state
   change) and on `app.releases` (every approval, including the first of two) raises
   `insufficient_privilege` when the signed-in person (`auth.uid()`) is a demo account and the
   dataset's kind isn't `demo`. System writes (no signed-in person) are untouched. The rule trusts
   the kind, so none of the app's roles can change a dataset's kind after it is made (a trigger), and neither `demo
   reset` nor `new-dataset --kind demo` makes a demo dataset against Ramp's sandbox.
3. **Demo accounts see only demo datasets and their cases** (restrictive row-level security
   policies on `app.datasets` and `app.cases`, so a permissive policy added later can't widen
   them). Alerts, holds and evidence rows stay readable to any signed-in person, as today; the
   API reaches them only through a dataset or case the person can see.
4. **The accounts are made by the team, not the seed.** The core has no Supabase admin key, so
   it can't create auth users. Runbook: create the two users in Supabase (generated passwords),
   `add_user.py --demo-only` for both (`--role approver --user-id …` for the approver),
   `dialback-core demo reset --link clerk=<demo clerk email>`, and set the four
   `DIALBACK_DEMO_*` variables on Vercel. Passwords never go in the repo.
5. **One click, on the sign-in page.** With `DIALBACK_DEMO_SIGN_IN=on` and both accounts'
   email and password set, the page offers "Sign in as the demo clerk" and "…approver". The
   server signs in with the stored password (Supabase, same throttle as sign-in); the browser
   never sees it. The landing page's demo links come here.
6. **Through the API a demo account never reaches the trigger:** it can't see a non-demo case, so
   every case route answers 404. The trigger is the defense for anything that gets past that.

**Stated limits:** the demo approver is shared, so anyone with the link can release or block on
the demo dataset; a `demo reset` restores it. Delete the accounts after Nov 19 (spec 0010).

## Contract touched

None.

## Acceptance

- [ ] A demo approver can't clear, block, approve or reject on a non-demo dataset; the case and
      hold don't move. The same account can on a demo dataset.
- [ ] A demo account sees no non-demo dataset or case; other accounts see everything as before.
- [ ] System events on a non-demo dataset are unaffected.
- [ ] The one-click buttons appear only when the flag and all four credentials are set, and only
      in Supabase mode; a failed sign-in shows the usual message.
- [ ] Through the API, a demo account gets 404 for a non-demo case and sees no non-demo dataset.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1–3 | `supabase/tests/015_demo_accounts.sql` |
| 4 | `apps/web/tests/auth.test.ts` (`demoAccounts`) |
| 5 | `apps/core/tests/test_api.py::test_a_demo_account_is_refused_outside_demo_data` |

## Out of scope

Creating Supabase users from code; per-judge accounts.

## Open questions

- Who holds the demo passwords and sets them on Vercel (Sami owns Supabase from Nov 8)?

## Commands

```bash
scripts/db/test.sh
cd apps/core && uv run pytest
cd apps/web && pnpm test && pnpm typecheck && pnpm build
```
