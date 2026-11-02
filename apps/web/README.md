# apps/web: the dashboard

**Owner: all three, one area each.** Next.js 16 and TypeScript, deployed on Vercel with a
preview for every pull request.

## Job

Show each case's evidence, take the release decision, and show the scorecard.

## Pages and owners

| Page | Route | Shows | Owner | Status |
|---|---|---|---|---|
| Shell and navigation | `src/app/d/[datasetId]/layout.tsx` | Who is signed in, the dataset, navigation, alert banners ("Hold not confirmed") | Abhi | built |
| Case queue | `/d/[datasetId]/cases` | Cases waiting on a person first, then in progress, each by soonest saved payment date; due-soon and deadline labels on the dataset's clock (spec 0009) | Abhi | built |
| Case detail | `/d/[datasetId]/cases/[caseId]` | The change, the hold and saved dates (due-soon flags), the decision grouped by rule (not verified, sent to a person, not shown, shown) with rule ids, the deadline, the second-approver state, the chain badge, the timeline from evidence (spec 0009) | Abhi | built |
| Release console | on case detail | A required reason, then approve or reject (and clear or block from review), sent with the version the person saw | Abhi | built |
| Number lineage | `/d/[datasetId]/cases/[caseId]/lineage` (suggested) | The chosen number with its sources, every rejected number with its reason | Inesh | to build |
| Vendor risk list | `/d/[datasetId]/risk` (suggested) | Vendors whose number on file is provable, unproven, a near miss, or missing | Inesh | to build |
| Call view | `/d/[datasetId]/calls/[callId]` (suggested) | The transcript with recorded answers, the recording, speech-guard trips | Sami | to build |
| Scorecard | `/d/[datasetId]/scorecard` (suggested) | The latest run's gates, rates with intervals, results by attack class | Sami | to build |

The routes for Inesh's and Sami's pages are suggestions; add a link in the shell's nav when a
page lands.

## How it works

- **Data:** server components call the core (`src/lib/core.ts`) with the signed-in user's
  JWT; the browser never calls the core. Types are generated from
  `packages/contracts/openapi/dialback.yaml` (`pnpm gen:api`; `pnpm check:api` fails if stale).
- **Actions:** release and review are server actions that forward the request under the
  person's own token with the case version they saw. The core runs it in Postgres as that
  person, so `app.release_case()` refuses the changer, the reviewer and a stale version no
  matter what the page shows. On success the page shows a templated notice; a refusal shows
  the core's templated reason inline.
- **Sign-in (spec 0010):**
  - **Supabase:** with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` set, people sign in with
    an email magic link.
    - `signInWithOtp` never creates accounts, and the page answers the same for any email.
    - The link lands on `/auth/confirm`, which signs in only when the person presses the button,
      because email scanners open links and each link works once.
    - `@supabase/ssr` keeps the session in httpOnly cookies, and `src/proxy.ts` refreshes it on
      every request.
    - The server forwards the access token to the core, which verifies it against the project's
      signing keys. The dashboard never decides who someone is.
  - **Dev:** `DIALBACK_AUTH_MODE=dev`, local only and refused when `VERCEL_ENV=production`,
    lists the users in `DIALBACK_DEV_USERS` and mints a one-hour JWT with `DIALBACK_JWT_SECRET`.
- **Text:** every sentence that explains a decision comes from the core's templates and
  says "provable" or "not provable", never "safe". Text people typed is shown as plain text.

## Run it

Start the stack (Postgres, the Ramp mock and the core, with a seeded demo dataset), then
the dashboard with the environment it prints:

```bash
uv run python scripts/dev/stack.py --changes
```

```bash
cd apps/web && CORE_URL=http://127.0.0.1:8001 DIALBACK_AUTH_MODE=dev DIALBACK_JWT_SECRET=... DIALBACK_DEV_USERS='...' pnpm dev
```

| Variable | Meaning |
|---|---|
| `CORE_URL` | The core service (default `http://localhost:8001`) |
| `SUPABASE_URL` | The Supabase project's URL, read at run time (not `NEXT_PUBLIC_`: nothing in the browser uses it) |
| `SUPABASE_PUBLISHABLE_KEY` | The project's publishable key (`sb_publishable_…`) |
| `DIALBACK_AUTH_MODE` | `dev` for the local sign-in; ignored when `VERCEL_ENV=production` |
| `DIALBACK_JWT_SECRET` | Dev mode only: the same secret the core verifies with |
| `DIALBACK_DEV_USERS` | Dev mode only: `uuid:Name,uuid:Name` |

### Setting up the Supabase project for sign-in

1. **JWT signing keys:** the project must sign with asymmetric keys. If it still uses the
   legacy secret, use "Migrate JWT secret", then "Rotate keys". The core gets only
   `SUPABASE_URL` (spec 0010, D4).
2. **URL configuration:** Site URL is the dashboard's production URL. Magic links go there, so
   preview deployments can't finish a sign-in.
3. **Email templates:**
   - **Magic link:** link to `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email`.
   - **Invite user:** the same with `type=invite`.
4. **Link lifetime:** set the email OTP expiry to 15 minutes. An unspent link's token is in the
   confirm page's URL, so it can reach request logs (spec 0010, stated limits).
5. **Invite-only:** turn off new-user sign-ups. Invite each person from the Users page, then
   give them a profile and a role in `app.user_profiles` and `app.user_roles` (spec 0010, D6).
6. **Email delivery:** the built-in email reaches only members of the project's Supabase
   organization. Anyone else needs custom SMTP (spec 0010, Q32).

To try the whole flow locally without a project, run the stand-in for Supabase Auth with the
stack:

```bash
uv run python scripts/dev/fake_supabase_auth.py --user <uuid>:riley@example.test
```

```bash
uv run python scripts/dev/stack.py --changes --supabase-url http://127.0.0.1:54321
```

```bash
cd apps/web && CORE_URL=http://127.0.0.1:8001 SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_PUBLISHABLE_KEY=sb_publishable_local pnpm dev
```

Magic links are printed by the stand-in instead of emailed. To check the whole flow in a
browser, run `pnpm build && pnpm start` instead of `pnpm dev` and use `scripts/dev/signin_e2e.cjs`.
Its header lists the steps.

## Checks

```bash
pnpm typecheck && pnpm test && pnpm build && pnpm check:api
```
