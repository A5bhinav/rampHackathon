# 0078: The Ramp mock matches Ramp's published API

**Owner:** Sami  **Slice:** money path (the mock) and contracts  **Target day:** before the Nov 19 final
**Needs sign-off from:** Abhi (owner of `apps/ramp-mock/`)

## Goal

The mock exists so the core can be built and tested before sandbox access (spec 0002, D1). Nothing
checks that it looks like Ramp, and in places it doesn't: it sends `account_number_last4` where
Ramp sends `account_number_last_four`, a vendor `hold` that Ramp's vendor has no field for, and a
hold object where Ramp answers 202 or 204 with no body. This spec commits Ramp's published OpenAPI
document and adds a test that checks every mock route that copies Ramp, and the mock's webhook,
against it. When the test passes, the core's tests against the mock say something about real Ramp.
It answers spec 0002 Q4 and core README question 6 in a form that can be re-checked.

This spec is the test only. Making the mock and the core pass it is the next spec.

## Contract touched

`packages/contracts/openapi/ramp-developer-api.json`: Ramp's Developer API document. Source
`https://docs.ramp.com/openapi/developer-api.json`, fetched 2026-11-10T09:09Z, OpenAPI 3.0.2, 189
paths, upstream SHA-256 `29571a47002f5d08d5c7686a31551bb9b66a6c40f06b66597b3f1f5b26d35808`.
It lands in its own pull request (commit `8767c97`), ahead of the test, because a contract changes
only in its own pull request (AGENTS.md).

One change from upstream, made by the command below and nothing else: Ramp's document carries four
example access tokens (in the `example` of `TokenRequestBody`, `TokenResponse` and
`TokenRevokeRequestBody`). They are documentation values, but a secret scanner that knows Ramp's
token format could block the push or raise an alert, so each becomes `ramp_business_tok_EXAMPLE`.
Committed SHA-256 `64321b18337bf39d10d86d1fa9903c24f48268cd8ea0d2bc2b6a04a602d458da`. To refresh:
download, run the command, update both hashes here and the committed one in the test.
`validate.py` checks only `openapi/*.yaml`, so it skips this file.

## Decisions

- **D1. What "matches" means,** for each mock route under `/developer/v1`:
  1. Ramp's document has the same path template and method.
  2. Each query parameter and request-body field the mock accepts is one Ramp documents for it.
  3. The mock's success status is one Ramp documents for it. If it isn't, the body is still
     compared with Ramp's success body when Ramp documents exactly one, so both problems show.
  4. The body validates against Ramp's schema for that status, under OpenAPI 3.0 rules, with
     `format` checked (uuid, date-time) and two changes made in code first:
     - an object schema that lists `properties` and doesn't set `additionalProperties` gets
       `additionalProperties: false`, so a field the mock sends must exist in Ramp by that name;
     - `required` is dropped, so the mock may leave out fields Ramp sends. The core can only
       read fields the mock sends, and those are what this checks.

     A 204 must have no body, although Ramp's document gives four 204s a JSON body. A body Ramp
     sends as something other than JSON (a PDF) is reported as one the checker can't compare.
  5. **A null is never a problem. Changed 2026-11-10, after the first run; Sami decided.** Ramp's
     own examples for these routes' schemas put null in six fields the schema calls non-nullable
     (`address_line_2`, `deep_link_url`, `remote_id`, `paid_at`, `merchant_id`,
     `billing_frequency`). The checker also rejected null where Ramp does mark a field nullable,
     through an `allOf` wrapper or an enum that doesn't list null (`user_details`, `payment`,
     `approval_status`). A field the mock sends that Ramp doesn't define is still named when its
     value is null. Before this change and the review fixes: 41 problem lines from 9 checks, 22 of
     them a null. After: 33 lines from 12 checks and the webhook. On the original 9 checks it is
     21: the 19 that weren't a null, and 2 bodies now compared under D1.3.
- **D2. Coverage.** A test lists the mock app's routes under `/developer/v1` and fails if one has
  no check. Each route is checked after a bank change; the vendor and the bills also on hold, and
  the audit log also for a change with no actor. A list that comes back empty fails: nothing was checked.
- **D3. The checker is tested on its own,** without the mock. It passes a Ramp-shaped body (a null in
  a nullable field, a Ramp-required field left out) and a null in each of the three forms in D1.5,
  and still names an unknown field set to null. It reports: an unknown field, a wrong type and
  format, a null body, an unknown path, method and status (naming Ramp's), query parameter and body
  field, a body that isn't JSON, a body on a 204, a body it can't compare, and the body behind a
  wrong status. A mutation check broke ten of its rules, and each broke a test.
- **D4. No network.** The test reads the committed file.
- **D5. Ramp's own examples pass.** Every object example on a schema these routes reach goes
  through the checker. Two contradictions in Ramp's document, checked by hand, are named in the
  test, which fails if they change: the `Bill` example has an `accounting_sync_date` that `Bill`
  doesn't define, and its accounting field selections carry a `type` that
  `ApiAccountingFieldSelection` doesn't define.
- **D6. Webhooks.** The mock's webhook body is checked against Ramp's
  `MockOutboundWebhookEventPayload` (`id`, `business_id`, `created_at`, `object`, `type`), and its
  headers for the `X-Ramp-Webhook-ID` Ramp's webhooks guide documents, equal to the payload's id.

## Acceptance

- [x] The committed document's SHA-256 is the one above, and it holds no string in any of Ramp's
      token formats (`ramp_` business or user, `tok` or `jwt`) but the four placeholders.
- [x] The checker passes and reports everything in D3, and Ramp's own examples pass (D5).
- [x] Every `/developer/v1` route the mock serves has a check, in each state in D2.
- [x] One check for each route and state, and one for the webhook (D6). Each lists every problem.
- [x] Fail closed: a route or status the checker can't find in Ramp's document, a body it can't
      compare, or a body that isn't JSON where Ramp documents JSON, is a failure, never a skip.
- [x] Expected today: route and webhook checks fail, and their output is the drift list for the next spec.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/ramp-mock/tests/test_matches_ramp.py::test_the_committed_document_is_unchanged` |
| 2 | `apps/ramp-mock/tests/test_matches_ramp.py::test_checker_*`, `::test_ramps_own_examples_pass` |
| 3 | `apps/ramp-mock/tests/test_matches_ramp.py::test_every_ramp_route_the_mock_serves_is_checked` |
| 4 (D1.2 reads the mock) | `apps/ramp-mock/tests/test_matches_ramp.py::test_mock_inputs_reads_what_the_mock_accepts` |
| 4, 5, 6 | `apps/ramp-mock/tests/test_matches_ramp.py::test_route_matches_ramp[...]`, `::test_webhook_matches_ramp` |

## Out of scope

- Fixing the mock and the core: the next spec, in Abhi's slice, with this test's output as its input.
  Renaming fields won't do it all: Ramp's vendor bank account has no routing number, which the
  core's account fingerprint (ADR-8) uses.
- Ramp's `webhooks.verification` challenge, and error responses (401, 404, 429). The `/_mock` routes.
- Inputs are compared by name only: not their types or limits (Ramp's `page_size` is 2 to 100),
  not the inputs Ramp requires, and not query parameters the core sends that the mock ignores.
- Ramp fields the mock leaves out (D1.4). If the core starts reading one, the mock must send it.
- An object built from `allOf` branches that each list properties would be judged wrongly (each
  branch bans the other's fields). None of these routes reaches one today.
- What only the sandbox shows: which events fire, who is named as the actor, and where real
  responses differ from Ramp's document.

## Open questions

- **Q128 (Abhi, Sami):** the route checks fail until the mock is fixed. Land this test with the mock
  fix, or first, with each failing check marked strict `xfail` so CI stays green and each fix must
  remove its mark?
- **Q129 (Abhi):** who refreshes Ramp's document, and how often? A changed hash fails the test.
- **Q130 (Abhi):** the route tests send a full, made-up account number (`000055556666`, as
  `test_mock_api.py` on main already does) because the mock's bank-change route needs one. The
  reviewer flags the rule against full account numbers in fixtures. Allowed for made-up numbers in
  the mock's tests, or should the mock's route take something else?

## Commands

```bash
# refresh Ramp's document (then update the hashes)
curl -sSL -o /tmp/ramp.json https://docs.ramp.com/openapi/developer-api.json
python3 -c 'import re,sys; d=open(sys.argv[1],"rb").read(); open(sys.argv[2],"wb").write(re.sub(rb"(ramp_[a-z]+_(?:tok|jwt)_)[A-Za-z0-9]+", rb"\1EXAMPLE", d))' /tmp/ramp.json packages/contracts/openapi/ramp-developer-api.json
cd apps/ramp-mock && uv run pytest -q tests/test_matches_ramp.py
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
