# 0077: A clerk changes a vendor's bank details from the dashboard (demo portal)

**Owner:** Abhi  **Slice:** money path  **Target day:** Nov 10–11

## Goal

For the live demo, a person plays the AP clerk and changes a vendor's bank details by hand, the
way the fraud happens: in Ramp, by a real employee, believing a request. Today that change only
enters through `dialback-core demo change`, which replays a prepared change from
`scripts/demo/seed.json`. This spec adds a **Ramp (mock) vendor portal** inside the dashboard. A
signed-in clerk picks a vendor, types a new routing and account number, and submits. The core
forwards the change to the Ramp mock **as that clerk's linked Ramp user**. From there everything is
the real pipeline, nothing staged: the mock's webhook, detection, the hold (R1), the Trust Ledger's
number (R2), the disclosed agent's call (R3, R4, R8), the decision, and a second person's release.
The clerk who entered the change is refused the release by Postgres (R5).

It is a demo tool: it works only on demo datasets, only against the Ramp mock, and Dialback still
never stores a full account number (ADR-8).

## Contract touched

`packages/contracts/openapi/dialback.yaml`, additive only, in its own PR:
- `GET /api/datasets/{dataset_id}/vendor-accounts`: each vendor with its current account (`AccountRef`: routing number and last four) and its open case, plus whether the signed-in person can make a change here and, if not, the core's sentence saying why.
- `POST /api/datasets/{dataset_id}/vendors/{vendor_id}/bank-change`: body `{routing_number, account_number}`. `202` returns the new account as an `AccountRef` (routing and last four only).
- `Error.code` grows by one value, `upstream_unavailable`, for when the Ramp mock refuses or is down.

## Acceptance

Core
- [ ] `GET /vendor-accounts` lists the dataset's vendors (row-level security as the person) with routing and last four only, and each vendor's open case id. `can_change` is true only on a current (not retired) demo dataset, with `DIALBACK_RAMP=mock`, for a person whose profile is linked to a Ramp user. When false, `reason` is the core's sentence.
- [ ] `POST /bank-change` refuses, with a templated sentence and nothing sent to Ramp:
  - signed out (401);
  - not a current demo dataset, or `DIALBACK_RAMP=sandbox` (403);
  - the person has no linked Ramp user (403);
  - an unknown vendor, or a vendor from another dataset (404);
  - a routing number that isn't 9 digits with a valid ABA checksum, or an account number that isn't 4 to 17 digits (422, without echoing either number).
- [ ] Otherwise it posts the change to the mock's `/_mock/vendors/{ramp_vendor_id}/bank-change` with `actor_user_id` set to the person's Ramp user, asks the mock to deliver its webhooks now, and returns 202 with routing and last four. If the mock fails, it returns 502 `upstream_unavailable`.
- [ ] The full account number is never written to a table, a log or an error message, and never returned.
- [ ] Detection, the hold, selection, the call and the decision are unchanged. The change arrives like any Ramp change, and `changed_by` is the clerk, so R5 refuses the clerk's own release.

Web
- [ ] AP workspace gets a **Ramp (mock)** page (`/d/{id}/ramp`) listing vendors with their current account (`026009593 ····0001`) and an "Edit payment details" link, and an edit page (`/d/{id}/ramp/{vendorId}`) with routing and account fields. Both are labelled as a stand-in for Ramp.
- [ ] Submitting goes through a server action to the core (the browser never calls the core). On success the page says the change was sent to Ramp and then watches, refreshing itself until Dialback opens the case. It then shows the case's seven stations, with links to the case and the live call. A refusal shows the core's message inline, and the typed numbers are not kept in the URL or the page.
- [ ] When `can_change` is false, the form is not offered, and the core's `reason` is shown.

Never
- [ ] No full account number in any table, log, URL, response or rendered page after submit. No dashboard-written sentence explaining a refusal: those are the core's.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| The two operations, their schemas and the new error code validate | `packages/contracts/scripts/validate.py` |
| `vendor-accounts` lists routing and last four, open cases, `can_change` and `reason` | `apps/core/tests/test_clerk_change.py` |
| Each refusal (signed out, not demo, retired, sandbox, not linked, unknown vendor, other dataset, bad routing, bad account) sends nothing to the mock | `apps/core/tests/test_clerk_change.py` |
| A good change reaches the mock as the linked Ramp user, the webhook is delivered, the case opens with `changed_by` the clerk, and the clerk is refused the release | `apps/core/tests/test_clerk_change.py` |
| The full account number appears in no table, response or log line | `apps/core/tests/test_clerk_change.py` |
| Routing checksum and account format | `apps/web/tests/ramp-portal.test.ts`, and the core test above |
| The portal's view: can change or not, the case it watches for | `apps/web/tests/ramp-portal.test.ts` |

## Follow-up: one command before the demo

The selection rule trusts only what was recorded before the case opened, so the Trust Ledger and
the change-request email must be in before the clerk saves a change; a change saved first can never
get a number ("The change request isn't in the ledger"). `dialback-core demo prepare --link
clerk=<email> [--phone acme=+1...] [--request acme]` does it in that order: reset, ledger load, request
email. Every check (the link, the phones, the vendor refs, the sandbox) runs before the reset.
`scripts/demo/prepare_hosted.sh <email>` runs it on Railway with the team phone read from the voice
service's dial allowlist, never printed. Tests: `apps/core/tests/test_demo.py::test_prepare_*`.

## Out of scope

- The vendor's change-request email in the portal (it needs a Trust Ledger write; for now the team runs `dialback-ledger demo request --vendor <ref>` before the change).
- Changing a vendor's phone numbers or contacts.
- Any non-demo dataset.

## Open questions

1. Spec numbers: Sami's branch now holds 0066, which spec 0063's renumbering had reserved for the guided-demo bar. That spec needs a new number (0078 or later).
2. Should the portal also offer "no known author" (a vendor-supplied or synced change), which needs two approvers (ADR-10)? Not in this spec.
3. Rate limit: the mock limits requests per IP. Is a per-person limit on dashboard changes wanted for the hosted demo?

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
cd apps/web && pnpm gen:api && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
cd apps/core && uv run pytest
python3 scripts/ci/boundaries.py
```
