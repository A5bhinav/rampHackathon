# 0096: Each dataset picks the Ramp mock or Ramp's sandbox, and the core logs in to Ramp

**Owner:** Sami  **Slice:** money path  **Target day:** before the Nov 19 final

## Goal

Today one server-wide flag, `DIALBACK_RAMP`, picks the Ramp mock or the sandbox for every dataset,
and the sandbox side isn't built: the core refuses to start with it. When this is done, each
dataset says which Ramp it uses, fixed when it is made, so one deployment can run the demo on the
mock and a dataset on Ramp's sandbox side by side, and the core logs in to Ramp's sandbox. It
serves R1 (a dataset can't switch Ramps mid-case, and two datasets can't hold the same real
vendor), R7 (anything unclear fails closed) and spec 0034 (demo datasets stay on the mock).

Not here: Ramp's real field names (spec 0078's drift list). Until they land, a sandbox dataset logs
in and reads vendors, but syncing its bank accounts fails, and fails closed.

## Contract touched

None. The dashboard API is unchanged; its settings row for Ramp stays "not reported yet".

## Decisions

- **D1. The setting.** `app.datasets.ramp` is `mock` or `sandbox`, not null, default `mock`, so every
  existing dataset is on the mock. The database refuses: changing it after the dataset is made; a
  demo dataset on the sandbox; and a second current (not retired) sandbox dataset, because the
  sandbox credentials reach one Ramp business, and two datasets would place and lift holds on the
  same vendors.
- **D2. What a server can reach.** `DIALBACK_RAMP` keeps its meaning: `mock` (the default) means
  the server has the mock (`RAMP_BASE_URL`, `MOCK_RAMP_SECRET`); `sandbox` means it has none. The
  sandbox is reachable when `RAMP_SANDBOX_CLIENT_ID` and `RAMP_SANDBOX_CLIENT_SECRET` are both set;
  one without the other stops the server, and so does `DIALBACK_RAMP=sandbox` without them.
  `RAMP_SANDBOX_BASE_URL` defaults to `https://demo-api.ramp.com`, and any other address is refused,
  so these credentials can never reach production Ramp. `RAMP_SANDBOX_SCOPES` defaults to the five
  the core uses (`vendors:read vendors:write bills:read bills:write audit_logs:read`). A read-only
  app sets the three read scopes, and the hold then fails closed.
- **D3. Routing.** `RampConnector.for_dataset(id)` still returns one `RampPort`, so no caller changes,
  and it is the dataset's one port: the same object every call, so a method swapped on it (the
  tests' faults and spies) is what the worker calls. On first use it reads the dataset's `ramp` (it
  never changes, so once) and talks to the mock with the dataset's mock token, or to the sandbox
  with the login. A dataset whose Ramp this server can't
  reach, or an unknown dataset, is a `RampError` that isn't retried. The demo's bank change from the
  dashboard (spec 0077) also requires the dataset to be on the mock.
- **D4. The login.** Client credentials: `POST {base}/developer/v1/token`, HTTP Basic auth with the
  id and secret, form fields `grant_type=client_credentials` and `scope`. The token is kept and
  renewed 5 minutes before `expires_in` (10 days) runs out, one renewal at a time. A 401 from Ramp
  drops the kept token and is retryable, so the worker's next try logs in again. A failed login is
  a `RampError`: retryable after a network error, 429 or 5xx; not after a refusal. The secret and
  the token never appear in a log line or an error message.
- **D5. Making a dataset.** `dialback-core new-dataset --ramp sandbox` (default `mock`). Refused
  before the database is touched: a demo dataset on the sandbox, and a sandbox dataset on a server
  without sandbox credentials.
- **D6. Webhooks.** Ramp signs each subscription with its own secret, and the core checks one
  server-wide `RAMP_WEBHOOK_SECRET`. So until webhook setup lands, a sandbox dataset relies on the
  poller (spec 0003). Don't register Ramp webhooks yet: they would fail the check and raise alerts.

## Acceptance

- [x] Every existing dataset reads `mock`. The database refuses a change to `ramp`, a demo dataset
      on the sandbox, and a second current sandbox dataset; a retired one doesn't count.
- [x] `create_dataset(..., ramp=...)` stores the setting.
- [x] Settings: the sandbox values are read; one credential without the other, an address other
      than `https://demo-api.ramp.com`, and `DIALBACK_RAMP=sandbox` without credentials each stop
      the server.
- [x] A mock dataset reaches the mock with its own token; a sandbox dataset reaches the sandbox with
      the login's token. A sandbox dataset on a server without credentials, and an unknown dataset,
      raise a `RampError` that isn't retried. The dataset's setting is read once, and each dataset
      has one port.
- [x] The login sends Basic auth and the form fields, keeps the token, renews it near expiry, renews
      once under concurrent calls, drops it on a 401 from Ramp, and classifies failures as in D4.
      No secret or token appears in an error message.
- [x] The dashboard's demo bank change refuses a sandbox dataset.
- [x] `new-dataset` refuses a demo dataset on the sandbox, and the sandbox without credentials.
- [x] Everything else is unchanged: the core and SQL suites pass.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1, 2 | `supabase/tests/016_dataset_ramp.sql`, `apps/core/tests/test_ramp_sandbox.py::test_create_dataset_*` |
| 3 | `apps/core/tests/test_ramp_sandbox.py::test_settings_*` |
| 4, 6 | `apps/core/tests/test_ramp_sandbox.py::test_routing_*` |
| 5 | `apps/core/tests/test_ramp_sandbox.py::test_login_*` |
| 7 | `apps/core/tests/test_ramp_sandbox.py::test_new_dataset_*` |
| 8 | `cd apps/core && uv run pytest`, `scripts/db/test.sh` |

## Out of scope

- Ramp's real field names (spec 0078), webhook setup, and the guard that limits the core to the
  three write calls it needs.
- Credentials per dataset (one Ramp business each), and production Ramp.
- Showing the setting in the dashboard: a contract change, in its own pull request.

## Open questions

- **Q201 (Sami):** for a real customer, credentials belong to the dataset (one Ramp business each),
  not the server. Which store: Supabase Vault, keyed by dataset?

## Commands

```bash
cd apps/core && uv run pytest -q
scripts/db/test.sh
python3 scripts/ci/boundaries.py
```
