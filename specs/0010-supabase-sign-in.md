# 0010: Supabase sign-in

**Owner:** Abhi  **Slice:** money path (dashboard shell and core API)  **Target day:** Tue Nov 3

## Goal

People sign in to the deployed dashboard with an email and password on accounts the team creates (Supabase Auth, design §7). The design named magic links; on Nov 2 the team chose passwords instead (decision 5). The core trusts a token only if the Supabase project's own keys signed it, then runs every query in Postgres as that person, so R5 is enforced on the real identity: approver role, and never the person who entered the change.

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

5. **The dashboard signs in with an email and password, using `@supabase/ssr`.** We chose this over magic links on Nov 2:
   - Supabase's built-in email reaches only members of our Supabase organization, so Ramp's judges couldn't sign in by link.
   - Our own email provider would need a domain, DNS records and deliverability work.
   - Links can be slow, land in spam or be opened by scanners on stage.

   Passwords need no email at all. In detail:
   - **Sign-in:** the page takes an email and a password (`signInWithPassword`).
     - An unknown email and a wrong password get the same answer.
     - Supabase being down gets its own answer ("not available"), and so does a rate limit. Neither depends on the email.
     - Each client gets 10 tries per 5 minutes in the app, plus a Vercel firewall rule on POST `/sign-in`.
   - **Session:** it lives in httpOnly cookies. `proxy.ts` refreshes it on each request (`getClaims`).
   - **Session length:** a sign-in lasts 12 hours from the moment the person signed in (decision 8).
     - **The clock:** it is the sign-in time inside the signed access token (`amr`), which the person can't change. A marker cookie set at sign-in is the fallback if a token lacks it.
     - **Expiry:** past 12 hours, `proxy.ts` ends the Supabase session (`signOut`, this session only) and deletes its cookies. The page then sends the person to sign in.
     - **No redirect from the proxy:** a server action's redirect is followed by an internal fetch that can't drop cookies, so a redirect there would loop.
   - **Calls to the core:** the server sends the session's access token. The dashboard never decides who someone is: the core verifies the token (decision 1).
   - **Sign-out** ends this browser's Supabase session only (`scope: "local"`), since the judges share accounts. It always deletes the cookies, even when Supabase can't be reached.

6. **Accounts are made by the team; nobody signs up.**
   - **No sign-ups, no email:** sign-ups are off in the project's Auth settings, and so is "Confirm email". Supabase never sends mail.
   - **Creating an account:** add each person on the Users page with their email and a generated password (16+ characters, never chosen by hand). Then give them a profile and a role in `app.user_profiles` and `app.user_roles` with `scripts/db/add_user.py`.
   - **Judges:** they get one approver account and one clerk account.
     - The clerk account is linked to the Ramp user who makes the demo's bank changes (`--ramp-user-id`). Otherwise every change has an unknown author and needs two approvers (ADR-10).
     - Their passwords are sent privately, never in the repo or the video, and the accounts are deleted after Nov 19.
   - **No role, no access:** a signed-in person with no role can see and do nothing, because the database grants decide.
   - **Removing a role** takes effect on their next request.

7. **The dev sign-in is unchanged** (`DIALBACK_AUTH_MODE=dev`, refused when `VERCEL_ENV=production`).

8. **The team's decisions (Nov 2)** on the questions this spec first left open:
   - **Q31, session length:**
     - **Access tokens:** they expire after 15 minutes, a Supabase setting. Signing out, or deleting someone, therefore ends their access within 15 minutes.
     - **Sign-ins:** each lasts at most 12 hours, measured from the token's own sign-in time, and the dashboard's proxy ends the session after that. Supabase's own session time-boxing needs a paid plan.
   - **Q32, email reach:** moot. With passwords, Supabase sends no email.
   - **Q33, MFA for approvers:** not for the hackathon. Approval already needs a second person, and the release always needs someone other than the changer. MFA is a production item.
   - **Q34, an admin role:** no admin page. The team grants roles with `scripts/db/add_user.py`, using `DATABASE_URL`. An admin role that manages users but can't release (design §7) is a production item.
   - **Q35, abuse:** no captcha.
     - **Guessing:** generated passwords of 16+ characters make guessing hopeless.
     - **Flooding:** Supabase limits `/token` requests per IP address, and every sign-in and session refresh comes from the dashboard's server, so all users share that limit.
       - A sustained flood could exhaust it for as long as it lasts. A refresh refused then signs people out, and they can't sign back in.
       - Two throttles keep one client from doing that: the app allows 10 tries per client per 5 minutes, and a Vercel firewall rule limits POST `/sign-in` per IP.
       - Supabase's IP forwarding would separate clients, but it needs a secret key on the dashboard, which we chose not to hold.

**Stated limits:**
- **Token lifetime.** Signing out, or deleting the user in Supabase, doesn't end an access token already issued: the core checks tokens locally, so a token stays valid until it expires, at most 15 minutes (decision 8). Removing a role is immediate.
  - Supabase's docs describe how to end it at once: check the token's `session_id` against `auth.sessions`. That needs a SQL function, so it is left for later.
- **Shared rate limit.** Every sign-in and refresh reaches Supabase from the dashboard's server. A flood from many addresses, past both throttles, could sign everyone out until it stops (decision 8, Q35).
- **The cap holds for the browser.** Someone who copies a person's cookies and refreshes the session outside the dashboard isn't stopped at 12 hours. Signing out, or deleting the account, ends that within 15 minutes.
- **Shared accounts.** The judges share one approver and one clerk account. The evidence log names the account, not the individual judge.

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
- [ ] With Supabase configured, the sign-in page asks for an email and a password. An unknown email and a wrong password get the same answer and start no session.
- [ ] A client past 10 tries in 5 minutes is told to wait, without reaching Supabase. Supabase being down gets its own message.
- [ ] The session refreshes on each request, and the core receives its access token. A 401 from the core sends the person back to sign in.
- [ ] A sign-in older than 12 hours, by the token's own sign-in time, ends: the session is signed out, its cookies are deleted, and the person signs in again.
- [ ] Signing out in one browser leaves another browser on the same account signed in.
- [ ] Sign-out ends the Supabase session and deletes its cookies even when Supabase can't be reached. The dev sign-in still works locally, and with neither configured the page says so.
- [ ] End to end, against a local stand-in for Supabase Auth: a password signs a seeded approver in, and the queue loads as that person.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| accepted, runs as the person | `apps/core/tests/test_auth.py::test_a_token_signed_with_the_projects_key_names_the_person`, `::test_an_rsa_signing_key_works_too`, `::test_the_api_runs_as_the_person_a_supabase_token_names` |
| refused | `test_auth.py::test_a_token_that_does_not_name_a_signed_in_person_of_this_project_is_rejected`, `::test_only_the_configured_key_sources_are_trusted`, `::test_a_token_whose_header_algorithm_does_not_match_its_key_is_rejected`, `::test_with_both_sources_a_token_without_the_projects_issuer_is_still_refused`, `::test_odd_headers_and_claims_are_handled` |
| key cache and rotation | `test_auth.py::test_keys_are_cached_and_an_unknown_key_id_refetches_at_most_once_in_a_while` |
| JWKS unreachable, concurrency, recovery | `test_auth.py::test_an_unreachable_jwks_keeps_recent_keys_and_then_fails_closed`, `::test_concurrent_first_requests_fetch_the_keys_once_and_an_outage_recovers` |
| settings | `test_auth.py::test_settings_need_a_key_source_and_derive_the_project_urls`, `::test_supabase_url_must_be_the_projects_https_address_alone`, `::test_a_deployed_core_never_trusts_a_committed_secret_or_the_shared_secret_by_accident` |
| dashboard sign-in, throttle, session, 12-hour cap, sign-out | `apps/web/tests/auth.test.ts`, which covers the mode choice, the email and password checks, the sign-in time from `amr`, the 12-hour cap and the throttle. The flow itself is checked end to end |
| end to end | `scripts/dev/signin_e2e.cjs` (Playwright) against `scripts/dev/fake_supabase_auth.py`, a stand-in for Supabase Auth, and `scripts/dev/stack.py --supabase-url`. It runs by hand, because CI has no browser job; its output goes in the PR |
| roles for created users | `scripts/db/test_add_user.py` (in the `py` CI job) |

## Out of scope

- A user-admin page and the admin role (decision 8, Q34).
- MFA for approvers (decision 8, Q33).
- Magic links and an email provider (decision 5).
- Realtime.
- Ending a session at once by checking `auth.sessions` (stated limits).

## Open questions

None. Q31 to Q35 were decided on Nov 2 (decision 8).

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
