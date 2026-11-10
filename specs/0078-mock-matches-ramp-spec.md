# 0078: The Ramp mock matches Ramp's published API

**Owner:** Sami  **Slice:** money path (the mock) and contracts  **Target day:** before the Nov 19 final
**Needs sign-off from:** Abhi (owner of `apps/ramp-mock/`)

## Goal

The mock exists so the core can be built and tested before sandbox access (spec 0002, D1). Nothing
checks that it looks like Ramp, and in places it doesn't: it sends `account_number_last4` where
Ramp sends `account_number_last_four`, a vendor `hold` that Ramp's vendor has no field for, and a
hold object where Ramp answers 202 or 204 with no body. This spec commits Ramp's published OpenAPI
document and adds a test that checks every mock route that copies Ramp against it. When the test
passes, the core's tests against the mock say something about real Ramp. It answers spec 0002 Q4
and core README question 6 in a form that can be re-checked.

This spec is the test only. Making the mock and the core pass it is the next spec.

## Contract touched

`packages/contracts/openapi/ramp-developer-api.json`: Ramp's Developer API document. Source
`https://docs.ramp.com/openapi/developer-api.json`, fetched 2026-11-10T09:09Z, OpenAPI 3.0.2, 189
paths, upstream SHA-256 `29571a47002f5d08d5c7686a31551bb9b66a6c40f06b66597b3f1f5b26d35808`.

One change from upstream, made by the command below and nothing else: Ramp's document carries
four example access tokens (`ramp_business_tok_...`, in the `example` of `TokenRequestBody`,
`TokenResponse` and `TokenRevokeRequestBody`). They are documentation values, but a secret
scanner that knows Ramp's token format could block the push or raise an alert, so each becomes
`ramp_business_tok_EXAMPLE`. Committed SHA-256
`64321b18337bf39d10d86d1fa9903c24f48268cd8ea0d2bc2b6a04a602d458da`. To refresh: download, run the
command, update both hashes here and the committed one in the test. `validate.py` checks only
`openapi/*.yaml`, so it skips this file.

## Decisions

- **D1. What "matches" means,** for each mock route under `/developer/v1`:
  1. Ramp's document has the same path template and method.
  2. Each query parameter and request-body field the mock accepts is one Ramp documents for it.
  3. The mock's success status is one Ramp documents for it.
  4. The body validates against Ramp's schema for that status, under OpenAPI 3.0 rules (so
     `nullable` works), with two changes made in code first:
     - an object schema that lists `properties` and doesn't set `additionalProperties` gets
       `additionalProperties: false`, so a field the mock sends must exist in Ramp by that name;
     - `required` is dropped, so the mock may leave out fields Ramp sends. The core can only
       read fields the mock sends, and those are what this checks.

  Both changes were decided before the first run. Ramp's schemas set `additionalProperties` only
  on `EmptyObject`, and require up to 27 fields an object (`Bill`).
- **D2. Coverage.** A test lists the mock app's routes under `/developer/v1` and fails if one has
  no check.
- **D3. The checker is tested on its own,** without the mock. It passes a Ramp-shaped body with a
  null in a nullable field and a Ramp-required field left out. It reports each of: an unknown
  field, a wrong type, an unknown path, method, status, query parameter and body field.
- **D4. No network.** The test reads the committed file.

## Acceptance

- [x] The committed document's SHA-256 is the one above, and it holds no token-shaped string.
- [x] The checker passes the Ramp-shaped body and reports each of the seven planted problems (D3).
- [x] Every `/developer/v1` route the mock serves has a check (D2).
- [x] One check for each route: `GET /vendors`, `GET /vendors/{vendor_id}`, `GET
      /vendors/{vendor_id}/accounts`, `GET /vendors/{vendor_id}/contacts`, `POST
      /vendors/{vendor_id}/hold`, `POST /vendors/{vendor_id}/release`, `GET /bills`, `PATCH
      /bills/{bill_id}`, `GET /audit-logs/events`. Each lists every problem it finds, not the first.
- [x] Fail closed: a route or status the checker can't find in Ramp's document, or a body that isn't
      JSON where Ramp documents JSON, is a failure, never a skip.
- [x] Expected today: route checks fail, and their output is the drift list for the next spec.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/ramp-mock/tests/test_matches_ramp.py::test_the_committed_document_is_unchanged` |
| 2 | `apps/ramp-mock/tests/test_matches_ramp.py::test_checker_*` |
| 3 | `apps/ramp-mock/tests/test_matches_ramp.py::test_every_ramp_route_the_mock_serves_is_checked` |
| 4 (D1.2 reads the mock) | `apps/ramp-mock/tests/test_matches_ramp.py::test_mock_inputs_reads_what_the_mock_accepts` |
| 4, 5, 6 | `apps/ramp-mock/tests/test_matches_ramp.py::test_route_matches_ramp[...]` |

## Out of scope

- Fixing the mock and the core: the next spec, in Abhi's slice, with this test's output as its input.
- Webhooks. Ramp's 3.0 document has no schema for outbound events. Known from Ramp's webhooks guide:
  the mock sends `data.vendor_id` and `X-Ramp-Event-Id`, Ramp sends `object` and `X-Ramp-Webhook-ID`,
  and Ramp first sends a `webhooks.verification` challenge.
- Error responses (401, 404, 429) and the `/_mock` routes.
- Ramp fields the mock leaves out (D1.4). If the core starts reading one, the mock must send it,
  and then this test checks it.
- What only the sandbox shows: which events fire, and who is named as the actor.

## Open questions

- **Q128 (Abhi, Sami):** the route checks fail until the mock is fixed. Land this test with the mock
  fix in one PR (the mock README's plan), or first, with each failing route marked strict `xfail` so
  CI stays green and each fix must remove its mark?
- **Q129 (Abhi):** who refreshes Ramp's document, and how often? A changed hash fails the test.

## Commands

```bash
# refresh Ramp's document (then update the hashes)
curl -sSL -o /tmp/ramp.json https://docs.ramp.com/openapi/developer-api.json
python3 -c 'import re,sys; d=open(sys.argv[1],"rb").read(); open(sys.argv[2],"wb").write(re.sub(rb"ramp_business_tok_[A-Za-z0-9]+", b"ramp_business_tok_EXAMPLE", d))' /tmp/ramp.json packages/contracts/openapi/ramp-developer-api.json
cd apps/ramp-mock && uv run pytest -q tests/test_matches_ramp.py
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
