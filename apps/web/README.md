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
    an email and password, on accounts the team creates.
    - No email is ever sent, so there's no email provider to set up, and anyone we give an
      account to can sign in, Ramp's judges included.
    - A wrong password and an unknown email get the same answer.
    - `@supabase/ssr` keeps the session in httpOnly cookies, and `src/proxy.ts` refreshes it on
      every request. A sign-in lasts 12 hours, timed from the signed token's own sign-in time;
      after that `src/proxy.ts` ends the session and the person signs in again.
    - Sign-out ends only this browser's session, since the judges share accounts.
    - Each client gets 10 sign-in tries per 5 minutes. Every sign-in reaches Supabase from our
      server, so this keeps one client from using up the rate limit everyone shares.
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
2. **Access-token expiry:** 15 minutes (900 seconds), in the project settings under JWT Keys.
   Signing out or deleting someone ends their access within that time (spec 0010, D8).
   Supabase advises against anything under 5 minutes.
3. **No sign-ups and no emails:** on the Auth Providers page, open the Email provider. Turn off
   "Confirm email", so accounts work without any email, and set the minimum password length to
   16. Then turn off new-user sign-ups, so nobody can create an account from the dashboard.
4. **Accounts:** for each person, add a user on the Authentication → Users page with their
   email and a generated password. Then give them a profile and a role with
   `scripts/db/add_user.py` (spec 0010, D6). Generate passwords; never pick them by hand:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(18))"
   ```

5. **Judges:** one approver account and one clerk account for Ramp's judges, so they can see the
   person who entered a change being refused the release.
   - Give the clerk account `--ramp-user-id` set to the Ramp user who makes the demo's bank
     changes. Without it, every change has an unknown author and needs two approvers (ADR-10),
     which one shared approver account can't provide.
   - Send the passwords privately, never in the repo or the video, and delete the accounts
     after Nov 19.
6. **Vercel firewall:** add a rate-limit rule for POST requests to `/sign-in`, for example 20 a
   minute per IP. Our in-app throttle counts per server instance, and this rule covers them
   all.

To try the whole flow locally without a project, run the stand-in for Supabase Auth with the
stack:

```bash
uv run python scripts/dev/fake_supabase_auth.py --user '<uuid>:riley@example.test:a-long-password'
```

```bash
uv run python scripts/dev/stack.py --changes --supabase-url http://127.0.0.1:54321
```

```bash
cd apps/web && CORE_URL=http://127.0.0.1:8001 SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_PUBLISHABLE_KEY=sb_publishable_local pnpm dev
```

To check the whole flow in a browser, run `pnpm build && pnpm start` instead of `pnpm dev`
and use `scripts/dev/signin_e2e.cjs`. Its header lists the steps.

## Checks

```bash
pnpm typecheck && pnpm test && pnpm build && pnpm check:api
```
