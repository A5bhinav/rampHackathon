# Deploying Dialback

Where each piece runs (design ADR-2), how to set it up once, and every variable it reads.
Secrets go into Railway and Vercel variables, never into the repo or a chat.

| Piece | Host | Config | Starts with | Health |
|---|---|---|---|---|
| Core API and worker (`apps/core`) | Railway, one always-on service | `apps/core/railway.json`, `apps/core/Dockerfile` | `dialback-core serve`, after `python scripts/db/migrate.py` | `GET /healthz` |
| Ramp mock (`apps/ramp-mock`) | Railway, private | `apps/ramp-mock/railway.json`, `apps/ramp-mock/Dockerfile` | `dialback-ramp-mock` | `GET /healthz` |
| Voice service (`apps/voice`, Sami) | Railway | added with the voice service | | |
| Dashboard (`apps/web`) | Vercel | `apps/web/vercel.json` | Next.js | |
| Database, sign-in | Supabase | `supabase/migrations/` | applied by the core's pre-deploy step | |

The `images` CI job builds both Railway images on every PR. It migrates a database from the core image twice (the second run must change nothing), then starts both services and waits for `/healthz`.

## 1. Supabase (once)

1. **Rotate the database password** if it was ever pasted anywhere (Project Settings → Database).
2. **Connection string for Railway: the Session pooler**, not the direct connection.
   - Copy it from Connect → Session pooler; it looks like `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   - The direct host (`db.<ref>.supabase.co`) is IPv6-only unless the IPv4 add-on is bought, and Railway connects out over IPv4.
   - Use the session pooler (port 5432), not the transaction pooler (6543): the migration runner holds an advisory lock, and the worker uses session settings.
3. **Sign-in** (spec 0010). Follow "Setting up the Supabase project for sign-in" in `apps/web/README.md`:
   - asymmetric JWT signing keys and a 15-minute access-token expiry;
   - "Confirm email" off, new sign-ups off, and a 16-character minimum password;
   - each person created on the Users page with a generated password, then given a role with `scripts/db/add_user.py`.
4. Migrations are applied by Railway (step 2).

## 2. Railway (once)

1. **Create a project, then a service from the GitHub repo** (`A5bhinav/rampHackathon`) for the core.
   - Name the service `core`.
   - In Settings, set **Config as code → Railway config file** to `/apps/core/railway.json`. Leave **Root directory** at `/`, because the Dockerfile needs the whole repo.
   - The config picks the Dockerfile, runs migrations before each deploy, starts the service, and checks `/healthz`.
2. **Add a second service from the same repo** for the mock.
   - Name it `ramp-mock`.
   - Set its config file to `/apps/ramp-mock/railway.json`.
   - Give it no public domain. Only the core talks to it, over Railway's private network.
3. **Networking:** generate a public domain for `core` only (Settings → Networking). The dashboard calls the core, and the voice service posts call reports to it.
4. **Variables:** paste the blocks below into each service's Variables → Raw editor. For the secrets, make shared variables in Project Settings → Shared Variables, so the two services can't drift.
   - Generate each secret with `openssl rand -hex 32`.
   - `MOCK_RAMP_SECRET` must be the same in both services.

Shared variables (Project Settings → Shared Variables):

```bash
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
MOCK_RAMP_SECRET=<openssl rand -hex 32>
```

`core` service:

```bash
DATABASE_URL=${{shared.DATABASE_URL}}
DIALBACK_RAMP=mock
RAMP_BASE_URL=http://${{ramp-mock.RAILWAY_PRIVATE_DOMAIN}}:8003
MOCK_RAMP_SECRET=${{shared.MOCK_RAMP_SECRET}}
RAMP_WEBHOOK_SECRET=<openssl rand -hex 32>
FINGERPRINT_KEY=<openssl rand -hex 32>
FINGERPRINT_KEY_VERSION=1
DIGITS_KEY=<openssl rand -hex 32>
DIGITS_KEY_VERSION=1
INTERNAL_API_TOKEN=<openssl rand -hex 32>
SUPABASE_URL=https://<ref>.supabase.co
PORT=8001
```

`ramp-mock` service:

```bash
DATABASE_URL=${{shared.DATABASE_URL}}
MOCK_RAMP_SECRET=${{shared.MOCK_RAMP_SECRET}}
PORT=8003
```

The `ramp-mock` URL uses Railway's reference variables (`${{service.VARIABLE}}`). If your service names differ, change `ramp-mock` in `RAMP_BASE_URL` to match.

**Keys never change once data exists.** `FINGERPRINT_KEY` and `DIGITS_KEY` hash bank accounts and spoken digits. Changing one means bumping its `_VERSION` and re-fingerprinting (ADR-8), so set them once.

### Core variables, all of them

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Supabase, through the session pooler |
| `RAMP_BASE_URL` | yes | The mock's private URL (or Ramp's API with `DIALBACK_RAMP=sandbox`, which isn't wired yet) |
| `DIALBACK_RAMP` | no | `mock` (default) |
| `MOCK_RAMP_SECRET` | with `mock` | Shared with the mock; per-dataset tokens are derived from it |
| `RAMP_WEBHOOK_SECRET` | yes | Verifies `X-Ramp-Signature`; at least 16 characters |
| `FINGERPRINT_KEY`, `FINGERPRINT_KEY_VERSION` | yes | Keyed fingerprints of bank accounts; at least 32 characters |
| `DIGITS_KEY`, `DIGITS_KEY_VERSION` | yes | Keyed hash of spoken digits; at least 32 characters |
| `INTERNAL_API_TOKEN` | yes | The voice service's bearer token for call reports; at least 24 characters. Give the same value to the voice service. |
| `VOICE_URL` | no | The voice service. Unset: every call is refused and the case goes to a person. |
| `DIALBACK_RESOLVER` | no | `stub` (default) or `ledger` |
| `SUPABASE_URL` | yes | The Supabase project's address, `https://<ref>.supabase.co`, with no path. The core verifies the dashboard's tokens with the project's public signing keys and issuer (spec 0010). |
| `DIALBACK_JWT_SECRET` | no | Leave unset in production. Supabase advises against trusting a shared secret, the core refuses one committed to the repo, and alongside `SUPABASE_URL` it needs `DIALBACK_TRUST_LEGACY_SECRET=1`, which is only for moving a project off its legacy secret. |
| `DIALBACK_CALLING_TZ`, `DIALBACK_CALLING_WINDOW`, `DIALBACK_CALLING_DAYS` | no | Default `America/Los_Angeles`, `09:00-16:30`, `mon-fri` |
| `DIALBACK_DEMO_CALLING_WINDOW` | no | Demo datasets only: `always` (default), `HH:MM-HH:MM` or `HH:MM-HH:MM mon-fri`, in `DIALBACK_CALLING_TZ`. An unreadable value stops the core at startup (spec 0011). |
| `CORE_PUBLIC_URL` | no | The core's public address, e.g. `https://core-production.up.railway.app`. A `demo reset` subscribes the mock's webhooks to it; unset, the poller finds each demo change within `DIALBACK_POLL_SECONDS`. |
| `DIALBACK_RUN_WORKER`, `DIALBACK_RUN_JOBS` | no | `0` turns either off |
| `DIALBACK_POLL_SECONDS`, `DIALBACK_RECONCILE_SECONDS`, `DIALBACK_FULL_SWEEP_SECONDS`, `DIALBACK_VERIFY_SECONDS` | no | Job intervals; default 60, 300, 86400 and 300 |
| `PORT`, `HOST` | no | Set by the image (8001; an empty `HOST` binds IPv4 and IPv6) |

## 3. Vercel (once)

1. **Add a new project from the GitHub repo** and set **Root Directory** to `apps/web`.
   - Vercel detects Next.js.
   - `apps/web/vercel.json` installs only the dashboard's packages from the pnpm workspace, and fails the build if the generated API types are stale.
   - Keep "Include files outside the root directory" on (the default), because the generated types live in `packages/contracts`.
2. **Variables** (Settings → Environment Variables):

| Variable | Preview | Production | Meaning |
|---|---|---|---|
| `CORE_URL` | yes | yes | The core's public Railway URL, `https://<core>.up.railway.app` |
| `SUPABASE_URL` | yes | yes | The same project address as the core's. It is read at run time, so it is not `NEXT_PUBLIC_`. |
| `SUPABASE_PUBLISHABLE_KEY` | yes | yes | The project's publishable key (`sb_publishable_…`) |
| `DIALBACK_AUTH_MODE` | | | Leave unset: Supabase sign-in is used. Dev sign-in (`dev`) is only for local runs, and is refused when `VERCEL_ENV=production`. |

Also add a Vercel Firewall rate-limit rule for POST requests to `/sign-in`, for example 20 a minute per IP (spec 0010, decision 8).

## 4. After the first deploy

- **Migrations.** Railway's deploy log shows `apply ...` lines, and later deploys show `0 applied now`. A file changed after it was applied stops the deploy. Add a new migration instead of editing an old one.
- **Status without deploying:** `railway run python scripts/db/migrate.py --status` from a linked checkout, or the same command in the core service's shell.
- **The demo company:** `dialback-core demo reset --link clerk=<the judges' clerk email>` in the core service's shell, then `dialback-core demo change --vendor acme` for beat 2. Set `CORE_PUBLIC_URL` first so the mock's webhooks reach the core. Each beat's command is in spec 0011.
- **Health:** `https://<core>/healthz` answers `{"ok": true}`.

## Things to check in Railway's UI

These follow Railway's documented config-as-code keys (`build.builder`, `build.dockerfilePath`, `build.watchPatterns`, `deploy.preDeployCommand`, `deploy.startCommand`, `deploy.healthcheckPath`, `deploy.restartPolicyType`). They couldn't be checked against the live docs from the build sandbox, so on the first deploy, confirm three things:

- The service shows the config file as its source.
- The pre-deploy step ran.
- The private hostname resolves.
