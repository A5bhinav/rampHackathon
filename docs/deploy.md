# Deploying Dialback

Where each piece runs (design ADR-2), how to set it up once, and every variable it reads.
Secrets go into Railway and Vercel variables, never into the repo or a chat.

| Piece | Host | Config | Starts with | Health |
|---|---|---|---|---|
| Core API and worker (`apps/core`) | Railway, one always-on service | `apps/core/railway.json`, `apps/core/Dockerfile` | `dialback-core serve`, after `python scripts/db/migrate.py` | `GET /healthz` |
| Ramp mock (`apps/ramp-mock`) | Railway, private | `apps/ramp-mock/railway.json`, `apps/ramp-mock/Dockerfile` | `dialback-ramp-mock` | `GET /healthz` |
| Voice service (`apps/voice`, Sami) | Railway, public (Twilio calls it) | `apps/voice/Dockerfile` | `dialback-voice` | `GET /healthz` |
| Dashboard (`apps/web`) | Vercel | `apps/web/vercel.json` | Next.js | |
| Database, sign-in | Supabase | `supabase/migrations/` | applied by the core's pre-deploy step | |

The `images` CI job builds the three Railway images on every PR. It migrates a database from the core image twice (the second run must change nothing), then starts all three services and waits for `/healthz`.

## 1. Supabase (once)

1. **Rotate the database password** if it was ever pasted anywhere (Project Settings → Database).
2. **Connection string for Railway: the Session pooler**, not the direct connection.
   - Copy it from Connect → Session pooler; it looks like `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   - The direct host (`db.<ref>.supabase.co`) is IPv6-only unless the IPv4 add-on is bought, and Railway connects out over IPv4.
   - Use the session pooler (port 5432), not the transaction pooler (6543): the migration runner holds an advisory lock, and the worker uses session settings.
3. **Sign-in** (spec 0010). Follow "Setting up the Supabase project for sign-in" in `apps/web/README.md`:
   - asymmetric JWT signing keys and a 15-minute access-token expiry;
   - "Confirm email" off, sign-ups on, and an 8-character minimum password (spec 0027);
   - people sign up on `/sign-up` and get clerk; approver and auditor are granted with `scripts/db/add_user.py --user-id`.
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
3. **Networking:** generate a public domain for `core` (the dashboard calls it) and for `voice` (Twilio calls it). The mock stays private. The voice service posts call reports to the core over the private network.
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
| `RAMP_BASE_URL` | with `mock` | The mock's private URL |
| `DIALBACK_RAMP` | no | `mock` (default): this server has the mock. `sandbox`: it has none, and needs the sandbox credentials. Each dataset picks its Ramp when it is made (`new-dataset --ramp`, spec 0096) |
| `MOCK_RAMP_SECRET` | with `mock` | Shared with the mock; per-dataset tokens are derived from it |
| `RAMP_SANDBOX_CLIENT_ID`, `RAMP_SANDBOX_CLIENT_SECRET` | for sandbox datasets | The Ramp sandbox app's client credentials. Both or neither. Seal the secret (a service variable on the core, not a shared one) |
| `RAMP_SANDBOX_BASE_URL` | no | Only `https://demo-api.ramp.com` (the default) is accepted, so these credentials never reach production Ramp |
| `RAMP_SANDBOX_SCOPES` | no | Default `vendors:read vendors:write bills:read bills:write audit_logs:read`; a read-only app sets the three read scopes |
| `DIALBACK_FULL_SWEEP_SECONDS` | for sandbox datasets | Set `60`, unless the server has a watch list (next row). A bank change waiting for review in Ramp moves no timestamp and writes no event, so only the sweep finds it, and the case should open within about two minutes (specs 0093, 0100). Default `86400` (once a day) |
| `RAMP_SANDBOX_WATCH_VENDORS` | demo only | Ramp vendor ids, separated by commas or spaces, read on every poll, so their bank changes are caught in seconds (spec 0102). Pair with `DIALBACK_POLL_SECONDS=5` and set `DIALBACK_FULL_SWEEP_SECONDS` to `86400` or unset it: jobs run one after another, so a `60` sweep would hold the poll to about once a minute. A pending change on any other vendor can then take up to a day to be found |
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
| `DIALBACK_EMAIL` | no | `off` (default) or `resend`. With `resend` the core answers `POST /webhooks/email`; without it the route is a 404. |
| `RESEND_API_KEY` | with `resend` | Resend's API key. Missing with `DIALBACK_EMAIL=resend`: the core refuses to start. |
| `RESEND_WEBHOOK_SECRET` | with `resend` | The webhook's signing secret from Resend (`whsec_...`). Missing with `resend`: the core refuses to start. |
| `DIALBACK_EMAIL_CLASSIFIER` | no | `rule` (default: the word rule alone flags an email, and no email text leaves Dialback) or `model` (a quarantined model alone decides the flag, pinned in `apps/core/src/dialback_core/email_door/classifier.py`, which sees the subject and text with every digit replaced by `#`). Any other value is `rule`. |
| `ANTHROPIC_API_KEY` | with `model` | The email classifier's key. Missing with `DIALBACK_EMAIL_CLASSIFIER=model`: the core refuses to start. |
| `DIALBACK_INBOX_ADDRESS` | no | The address vendors write to (shown on the public page) |
| `DIALBACK_REPLY_FROM` | no | The sender of replies. Unset: replies are not configured and none is sent. |
| `DIALBACK_BANK_CHANGES_URL` | no | The public page vendors are pointed to |
| `DIALBACK_COMPANY_NAME` | no | Named in replies and on the public page; default `the company you're writing to` |
| `DIALBACK_RUN_WORKER`, `DIALBACK_RUN_JOBS` | no | `0` turns either off |
| `DIALBACK_POLL_SECONDS`, `DIALBACK_RECONCILE_SECONDS`, `DIALBACK_FULL_SWEEP_SECONDS`, `DIALBACK_VERIFY_SECONDS` | no | Job intervals; default 60, 300, 86400 and 300 |
| `PORT`, `HOST` | no | Set by the image (8001; an empty `HOST` binds IPv4 and IPv6) |

### The email door: Resend (spec 0085)

Vendors email bank changes to an inbox; Resend receives the mail and POSTs a signed notice to the core. In Resend:

1. Turn on receiving for the inbox's domain (Resend gives the MX record to add).
2. Add a webhook for the `email.received` event to `https://<core>/webhooks/email`.
3. Copy the webhook's signing secret (`whsec_...`) into `RESEND_WEBHOOK_SECRET` on the `core` service, set `RESEND_API_KEY`, and set `DIALBACK_EMAIL=resend`.

The route verifies Resend's Svix signature over the raw body, stores the notice, and answers at once. It acts only on the current demo dataset: with no current demo it answers 503 and Resend retries.

### The voice service (specs 0029 and 0030)

A third service from the same repo, named `voice`. In its Settings: Dockerfile path
`apps/voice/Dockerfile`, root directory `/`, start command `dialback-voice`, health check
`/healthz`, watch paths `apps/voice/**`, `packages/policy/**`, `pyproject.toml`, `uv.lock` (the
same way the core's settings are applied; Railway no longer reads `railway.json`). Give it a public domain: Twilio fetches its instructions, posts call statuses and
opens the ConversationRelay WebSocket there. The core calls it over the private network. It runs no
migrations; it uses the database the core migrates.

`voice` service. The team sets the secrets; they never go in the repo or a chat:

```bash
DATABASE_URL=${{shared.DATABASE_URL}}
DIALBACK_INTERNAL_TOKEN=${{core.INTERNAL_API_TOKEN}}
DIALBACK_CORE_URL=http://${{core.RAILWAY_PRIVATE_DOMAIN}}:8001
DIALBACK_PUBLIC_BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
DIALBACK_WS_BASE_URL=wss://${{RAILWAY_PUBLIC_DOMAIN}}
DIALBACK_CALL_TOKEN_KEY=<openssl rand -hex 32>
DIALBACK_CUSTOMER_NAME=<the customer the disclosure names>
DIALBACK_AP_CALLBACK_NUMBER=<the AP callback number the disclosure gives, E.164>
DIALBACK_DIAL_ALLOWLIST=<the team phones, comma-separated E.164>
DIALBACK_VOICE=live
TWILIO_ACCOUNT_SID=<from Twilio>
TWILIO_AUTH_TOKEN=<from Twilio>
TWILIO_FROM_NUMBER=<the Twilio number, E.164>
ANTHROPIC_API_KEY=<the voice agent's key>
PORT=8080
```

On the `core` service, add:

```bash
VOICE_URL=http://${{voice.RAILWAY_PRIVATE_DOMAIN}}:8080
DIALBACK_RESOLVER=ledger
```

`DIALBACK_RESOLVER=ledger` makes the core use the Trust Ledger's pick, the number the voice
service reads and dials. With `stub`, the core's pick and the voice service's disagree, and every
call is refused `number_not_selected`.

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Without it the service refuses to boot |
| `DIALBACK_INTERNAL_TOKEN` | yes | The core's `INTERNAL_API_TOKEN`: authenticates `POST /calls` and the call reports |
| `DIALBACK_VOICE` | no | `text` (default: accepts calls, dials nobody) or `live` |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `DIALBACK_CALL_TOKEN_KEY`, `DIALBACK_CORE_URL`, `DIALBACK_PUBLIC_BASE_URL`, `DIALBACK_WS_BASE_URL` | for `live` | All of them, or the service stays on the text line and logs which are missing |
| `ANTHROPIC_API_KEY` | for `live` | The voice agent. Without it a live call runs the scripted placeholder (logged) |
| `DIALBACK_CUSTOMER_NAME`, `DIALBACK_AP_CALLBACK_NUMBER` | for `live` | Named in the disclosure (R8). Missing: the live path stays off and the service runs the text line (logged) |
| `DIALBACK_DIAL_ALLOWLIST` | for any call | Comma-separated E.164. Every dialed number must be on it, in every dataset (AGENTS.md). Empty: nobody is called |
| `DIALBACK_CALLING_TZ` | no | `America/Los_Angeles`. Non-demo datasets call weekdays 9:00 to 16:30 in this zone, fixed here; keep the core's `DIALBACK_CALLING_*` at their defaults or calls are refused. An unknown zone stops the service at startup |
| `PORT`, `HOST` | no | Set by the image (8080; an empty `HOST` binds IPv4 and IPv6) |

For a demo call to ring a team phone, the ledger's pick for the vendor must be that phone, and the
phone must be on the allowlist: `dialback-ledger demo load --phone acme=+1...` after `demo reset`.
The whole first call, step by step, is `docs/live-call.md`.

## 3. Vercel (once)

1. **Add a new project from the GitHub repo** and set **Root Directory** to `apps/web`.
   - Vercel detects Next.js.
   - `apps/web/vercel.json` installs only the dashboard's packages from the pnpm workspace, and fails the build if the generated API types are stale.
   - Keep "Include files outside the root directory" on (the default), because the generated types live in `packages/contracts`.
   - `apps/web/vercel.json`'s `ignoreCommand` (`apps/web/scripts/vercel-ignore-build.sh`) cancels the build when nothing the dashboard reads (`apps/web`, `packages/contracts`, the root `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `.npmrc`) changed since the branch's last successful deployment. A branch's first deployment, a redeploy of the same commit, and anything the script can't check all build. A canceled build still counts toward the Hobby plan's 100 deployments a day ([Vercel's docs](https://vercel.com/docs/project-configuration/project-settings#ignored-build-step)): it saves build time and the build slot, not quota. To force a build of a commit it skipped, redeploy with "Use project's Ignore Build Step" unchecked.
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
- **The demo company:** `dialback-core demo reset --link clerk=<the judges' clerk email>` in the core service's shell, then `dialback-core demo status` as the pre-flight check and `dialback-core demo change --vendor acme` for beat 2. Rehearse the whole run locally first with `uv run python scripts/dev/demo_rehearsal.py`. Set `CORE_PUBLIC_URL` first so the mock's webhooks reach the core. Each beat's command is in spec 0011.
- **Health:** `https://<core>/healthz` answers `{"ok": true}`.

## Things to check in Railway's UI

These follow Railway's documented config-as-code keys (`build.builder`, `build.dockerfilePath`, `build.watchPatterns`, `deploy.preDeployCommand`, `deploy.startCommand`, `deploy.healthcheckPath`, `deploy.restartPolicyType`). They couldn't be checked against the live docs from the build sandbox, so on the first deploy, confirm three things:

- The service shows the config file as its source.
- The pre-deploy step ran.
- The private hostname resolves.
