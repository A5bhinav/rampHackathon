# 0010: Supabase sign-in

**Owner:** Abhi  **Slice:** money path (dashboard shell and core API)  **Target day:** Tue Nov 3

## Goal

People sign in to the deployed dashboard with an email magic link (Supabase Auth, design §7). The core trusts a token only if the Supabase project's own keys signed it, then runs every query in Postgres as that person, so R5 is enforced on the real identity: approver role, and never the person who entered the change.

Today the only sign-in is the local dev one. It is off in production, so a production deploy can't be used.

## Decisions

1. **The core verifies every token itself.** Its key sources, configured by environment:
   - **`SUPABASE_URL`**: the project's public signing keys, from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. Only ES256 and RS256, the algorithms Supabase offers.
   - **`DIALBACK_JWT_SECRET`**: a shared HS256 secret, for local dev or a project still on Supabase's legacy secret.

   At least one source must be set. The token's `alg` picks the source, and each source accepts only its own algorithms. A public key can therefore never be used as an HMAC secret, and an unsigned token is never accepted. The key must also match the token's `kid` and its algorithm. A header that isn't the expected shape is refused, never an error.

   A core built from its environment, which is how it runs when deployed, refuses three things at start:
   - a `SUPABASE_URL` that isn't the project's `https://` address alone, with no path, user, query or fragment;
   - a `DIALBACK_JWT_SECRET` committed to this repo for the local stack;
   - both sources together, unless `DIALBACK_TRUST_LEGACY_SECRET=1` is also set.

   The dev stack builds the core's settings in code, so only it can point the core at a local Supabase or the stand-in.

2. **The claims must name a signed-in person of this project:**
   - `exp` is in the future;
   - `aud` is `authenticated`;
   - `role` is `authenticated`, which rules out the anon and service-role keys;
   - `is_anonymous` is not true;
   - `sub` is a user id;
   - when `SUPABASE_URL` is set, `iss` is `{SUPABASE_URL}/auth/v1`. A dev token, which has no issuer, is refused wherever `SUPABASE_URL` is set.

   Anything else answers 401 `unauthenticated`. The reason is logged and the token never is.

3. **Signing keys are cached as Supabase's docs advise:**
   - refreshed every 10 minutes;
   - an unknown `kid` refetches, at most once per 30 seconds, which is also how a rotated-in key is found;
   - while the JWKS can't be reached, the last keys work for at most 20 minutes after their fetch. Then nothing verifies until a fetch succeeds (fail closed, R7);
   - requests that arrive during a fetch wait for it rather than being refused.

   Key age is measured on a clock that keeps counting while the machine is suspended. A revoked key therefore stops working here within 20 minutes. Restarting the core empties the cache.

4. **Production uses asymmetric keys and no shared secret.**
   - Set `SUPABASE_URL` on the core and leave `DIALBACK_JWT_SECRET` unset. Supabase advises against verifying with the shared secret.
   - If the project is still on the legacy secret, use "Migrate JWT secret" and then "Rotate keys" on its JWT signing keys page first. While tokens signed with both are still live, the core can take both sources with `DIALBACK_TRUST_LEGACY_SECRET=1`. It logs a warning while it does.

5. **The dashboard signs in by magic link with `@supabase/ssr`:**
   - **Request:** the sign-in page takes an email and calls `signInWithOtp` with `shouldCreateUser: false`. It answers the same way whether or not the email has an account. The link is requested after the page has answered (`after()`), so the response time doesn't reveal it either.
   - **Link:** the email links to `/auth/confirm?token_hash=…&type=email`. That page signs in only when the person presses its button (`verifyOtp`), because email scanners open links and would use up a one-time token.
   - **Session:** it lives in httpOnly cookies. `proxy.ts` refreshes it on each request (`getClaims`).
   - **Calls to the core:** the server sends the session's access token. The dashboard never decides who someone is: the core verifies the token (decision 1).
   - **Sign-out** ends the Supabase session and always deletes its cookies, even when Supabase can't be reached.

6. **Invite-only.**
   - Sign-ups are off in the project's Auth settings, with `shouldCreateUser: false` as a second guard.
   - A person needs a Supabase invite plus a profile and a role in `app.user_profiles` and `app.user_roles`, added with `scripts/db/add_user.py`.
   - A signed-in person with no role can see and do nothing, because the database grants decide.
   - Removing a role takes effect on their next request.

7. **The dev sign-in is unchanged** (`DIALBACK_AUTH_MODE=dev`, refused when `VERCEL_ENV=production`).

**Stated limits:**
- **Token lifetime.** Signing out, or deleting the user in Supabase, doesn't end an access token already issued: the core checks tokens locally, so a token stays valid until it expires (Supabase's default is one hour). Removing a role is immediate. See Q31.
- **Email delivery.** Supabase's built-in email sends only to members of the project's Supabase organization. Anyone else needs custom SMTP (Q32).
- **Preview deployments.** Magic links go to the project's Site URL, so preview deployments can't complete a sign-in.
- **Request logs.** An unspent link's `token_hash` is in the confirm page's URL, so it reaches the host's request logs. Keep the project's email OTP expiry short; 15 minutes is enough.
- **Link requests.** Anyone can ask for links to any address: Supabase allows one per address per minute, and each new link replaces the last. Repeated requests can keep a person from signing in, and many addresses can use up the project's email quota (Q35).

## Contract touched

None. `dialback.yaml` already answers 401 `unauthenticated` on every dashboard endpoint.

## Acceptance

- [ ] An ES256 or RS256 token from the project's keys is accepted, and the API runs as the person it names.
- [ ] Refused with 401:
  - an expired token, a wrong audience, a wrong or missing issuer;
  - role `service_role` or `anon`, an anonymous sign-in, a subject that isn't a user id;
  - an unsigned token, an HS256 token when only the JWKS is trusted, an HS256 token "signed" with the public key;
  - a header algorithm that doesn't match its key.
- [ ] Keys are fetched once for many requests. An unknown `kid` refetches at most once per 30 seconds, and a rotated-in key is found.
- [ ] With the JWKS unreachable, recent keys work for up to 20 minutes, then nothing verifies.
- [ ] Settings need a key source, accept only the project's `https://` address, and derive the issuer and JWKS URL from it. They refuse a committed dev secret, and both sources together without `DIALBACK_TRUST_LEGACY_SECRET=1`.
- [ ] Concurrent first requests fetch the keys once and all succeed. After an outage, the next fetch restores access.
- [ ] With Supabase configured, the sign-in page asks for an email, gives the same answer for any email, and never creates an account.
- [ ] A link signs in only after the button is pressed. A bad or used link shows a message and starts no session.
- [ ] The session refreshes on each request, and the core receives its access token. A 401 from the core sends the person back to sign in.
- [ ] Sign-out ends the Supabase session and deletes its cookies even when Supabase can't be reached. The dev sign-in still works locally, and with neither configured the page says so.
- [ ] End to end, against a local stand-in for Supabase Auth: a magic link signs a seeded approver in, and the queue loads as that person.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| accepted, runs as the person | `apps/core/tests/test_auth.py::test_a_token_signed_with_the_projects_key_names_the_person`, `::test_an_rsa_signing_key_works_too`, `::test_the_api_runs_as_the_person_a_supabase_token_names` |
| refused | `test_auth.py::test_a_token_that_does_not_name_a_signed_in_person_of_this_project_is_rejected`, `::test_only_the_configured_key_sources_are_trusted`, `::test_a_token_whose_header_algorithm_does_not_match_its_key_is_rejected`, `::test_with_both_sources_a_token_without_the_projects_issuer_is_still_refused`, `::test_odd_headers_and_claims_are_handled` |
| key cache and rotation | `test_auth.py::test_keys_are_cached_and_an_unknown_key_id_refetches_at_most_once_in_a_while` |
| JWKS unreachable, concurrency, recovery | `test_auth.py::test_an_unreachable_jwks_keeps_recent_keys_and_then_fails_closed`, `::test_concurrent_first_requests_fetch_the_keys_once_and_an_outage_recovers` |
| settings | `test_auth.py::test_settings_need_a_key_source_and_derive_the_project_urls`, `::test_supabase_url_must_be_the_projects_https_address_alone`, `::test_a_deployed_core_never_trusts_a_committed_secret_or_the_shared_secret_by_accident` |
| dashboard sign-in, link, session, sign-out | `apps/web/tests/auth.test.ts`, which covers the mode choice, link parameters and email cleanup; the flow itself is checked end to end |
| end to end | `scripts/dev/signin_e2e.cjs` (Playwright) against `scripts/dev/fake_supabase_auth.py`, a stand-in for Supabase Auth, and `scripts/dev/stack.py --supabase-url`. It runs by hand, because CI has no browser job; its output goes in the PR |
| roles for invited users | `scripts/db/test_add_user.py` (in the `py` CI job) |

## Out of scope

- A user-admin page. The design's admin role, which manages users but can't release, isn't in `app.user_role` yet; see Q34.
- MFA for approvers.
- Realtime.
- Setting up custom SMTP.
- Checking with Supabase on every request whether a session was revoked.

## Open questions

- **Q31 (team):** how long should sessions last?
  - **Access tokens:** Supabase's default is one hour. A shorter expiry, such as 15 minutes, narrows the window after a sign-out or a deleted user, at the cost of more refreshes. It is set on the project's JWT settings page.
  - **Refresh tokens:** by default they don't expire, and the session cookies last 400 days, so a browser stays signed in until someone signs out. Supabase's session settings can time-box sessions or end them after inactivity.
- **Q32 (team):** will anyone outside our Supabase organization sign in, for example Ramp's judges at the final? If so, we need custom SMTP, because the built-in email won't reach them.
- **Q33 (team):** should approvers need MFA (`aal2`) before they can release? The design doesn't say.
- **Q34 (team):** the design lists admin and lab roles, but `app.user_role` has only clerk, approver and auditor. Until an admin role exists, whoever holds `DATABASE_URL` grants roles with `scripts/db/add_user.py`.
- **Q35 (team):** should the sign-in page add a captcha, which Supabase supports, and our own per-address limit before the live final? Without them, anyone who knows an approver's email can keep replacing their link (see the stated limits).

## Commands

```bash
cd apps/core && uv run pytest
```

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```

```bash
uv run python scripts/dev/stack.py --smoke
```
