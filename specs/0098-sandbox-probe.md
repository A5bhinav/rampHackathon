# 0098: A read-only probe of Ramp's sandbox

**Owner:** Sami  **Slice:** money path (tooling)  **Target day:** before the Nov 19 final  **Builds on:** 0097

## Goal

Spec 0097's translation layer was written from Ramp's document, which contradicts itself in places
(spec 0078, D5), and three of its readings only the sandbox can confirm (Q202 to Q204). The probe
logs in to Ramp's sandbox with the developer's own credentials, reads one vendor through the real
layer, and reports what the core would see, where Ramp's answers differ from its document, and
what it shows for Q202 to Q204. It changes nothing in Ramp or in Dialback.

## Contract touched

None.

## Decisions

- **D1. Read only, enforced twice.** The probe asks Ramp only for the `:read` scopes among those
  configured, and its HTTP client refuses any request other than a GET or the login's POST.
- **D2. Credentials** come from the developer's shell (`RAMP_SANDBOX_CLIENT_ID`,
  `RAMP_SANDBOX_CLIENT_SECRET`, optional `RAMP_SANDBOX_SCOPES`), checked as the core checks them
  (only `demo-api.ramp.com`). Missing ones print how to set them. No secret or token is printed.
- **D3. What it reads**, for one vendor (`--vendor`, or the first one Ramp lists): the vendor, its
  bank accounts, its bills, and every audit event filed under its id. Each through the layer, and
  each raw answer compared with Ramp's document, top-level fields only: a field the document doesn't
  define, and a null where it says not nullable.
- **D4. What it answers.** Q202: hold events under the vendor's id, and the hold the layer reads.
  Q203: each unpaid bill's `status_summary` and payment date, and whether the worker's read-back
  would pass. Q204: bank-change events, their `event_details` keys, and whether any value names one
  of the vendor's account ids. An answer the layer rejects (a malformed account) is reported, not
  fatal.
- **D5. Ramp's document** is the repo's copy when present (`packages/contracts/openapi/`, spec
  0078), else downloaded from `docs.ramp.com`; if neither works, the comparison is skipped and says so.

## Acceptance

- [x] Every request but the login is a GET; the client refuses any other; the login asks only for read scopes.
- [x] Missing credentials print how to set them; neither the secret nor the token appears in the output.
- [x] Findings report the layer's view, the document differences, and the answers to Q202 to Q204,
      from a fake sandbox with a hold and a bank change in it.
- [x] An account the layer rejects is reported, and the probe carries on.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-4 | `apps/core/tests/test_ramp_probe.py::test_*` |

## Out of scope

Placing a hold or making a change (do those by hand in `demo.ramp.com`, then run the probe); more
than one vendor; nested fields in the document comparison.

## Commands

```bash
export RAMP_SANDBOX_CLIENT_ID='...'
read -rs RAMP_SANDBOX_CLIENT_SECRET && export RAMP_SANDBOX_CLIENT_SECRET
uv run --package dialback-core python -m dialback_core.ramp.probe [--vendor <ramp vendor id>]
cd apps/core && uv run pytest -q tests/test_ramp_probe.py
```
