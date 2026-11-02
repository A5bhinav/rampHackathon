# apps/web: the dashboard

**Owner: all three, one area each.** Next.js 16 and TypeScript, deployed on Vercel with a
preview for every pull request.

## Job

Show each case's evidence, take the release decision, and show the scorecard.

## Pages and owners

| Page | Route | Shows | Owner | Status |
|---|---|---|---|---|
| Shell and navigation | `src/app/d/[datasetId]/layout.tsx` | Who is signed in, the dataset, navigation, alert banners ("Hold not confirmed") | Abhi | built |
| Case queue | `/d/[datasetId]/cases` | Unfinished cases by state and age, held bills and the next payment date | Abhi | built |
| Case detail | `/d/[datasetId]/cases/[caseId]` | The change, the hold and saved dates, the decision with every rule that fired, the chain badge, the timeline from evidence | Abhi | built |
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
- **Sign-in:** `DIALBACK_AUTH_MODE=dev` (local only; refused when `VERCEL_ENV=production`)
  lists the users in `DIALBACK_DEV_USERS` and mints a one-hour JWT with
  `DIALBACK_JWT_SECRET`, which the core verifies exactly as it would a Supabase token.
  Supabase magic-link sign-in comes once the Supabase project exists (spec 0002, Q5).
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
| `DIALBACK_AUTH_MODE` | `dev` for local sign-in; anything else shows Supabase sign-in (not configured yet) |
| `DIALBACK_JWT_SECRET` | Dev mode only: the same secret the core verifies with |
| `DIALBACK_DEV_USERS` | Dev mode only: `uuid:Name,uuid:Name` |

## Checks

```bash
pnpm typecheck && pnpm build && pnpm check:api
```
