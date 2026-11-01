# apps/web: the dashboard

**Owner: all three, one area each.** Next.js and TypeScript on Vercel, a preview deploy for
every pull request.

## Job

Show each case's evidence, take the release decision, and show the scorecard.

## How it works

- Supabase Auth with email magic links. Roles: clerk, approver, auditor, admin, plus a lab role for the release bot. An admin manages users but cannot release.
- Pages call only the core API (`packages/contracts/openapi/dialback.yaml`), sending the signed-in user's token. The API passes that token through to Postgres, so a release reaches `app.release_case()` under the approver's own identity and the database itself knows who is releasing (ADR-9).
- Supabase Realtime on `app.cases` and `app.alerts` for live updates (the publication is set up by the `app` migration).
- Every sentence that explains a decision comes from a template in code, never from a model, and says "provable" or "not provable", never "safe".
- Document text shown anywhere is escaped (OWASP LLM05). The changer never sees the release button, and SQL refuses them anyway.

## Pages and owners

| Page | Shows | Owner |
|---|---|---|
| Case queue | Unfinished cases by state and age, and held bills near their due date | Abhi |
| Case detail | A timeline built from evidence events, the chain-intact badge (`audit.verify_chain`), and the decision with the rules that fired | Abhi |
| Release console | The evidence summary, a required reason, and release or reject (`app.release_case`) | Abhi |
| Number lineage | The chosen number with its sources and first-seen dates, and every rejected number with its reason (`ledger.lineage`) | Inesh |
| Vendor risk list | The opening screen: vendors whose number on file is provable, unproven, a near miss, or missing (`ledger.risk_list`) | Inesh |
| Call view | The transcript with recorded answers highlighted, the recording (signed link), and any speech-guard trips | Sami |
| Scorecard | The latest run's gates, rates with intervals, results by attack class, and what changed since the last run | Sami |

## Start here

1. `pnpm create next-app@latest apps/web --ts --app --eslint` (or your preferred flags), then add `@supabase/ssr` and `@supabase/supabase-js`.
2. In the Supabase project's API settings, expose the `app` and `audit` schemas (and `ledger`, `voice`, `lab` as they land); `public` has nothing in it.
3. Generate the API client from `dialback.yaml` into `packages/contracts/generated/ts` (never edit it by hand) and point it at Prism until the endpoint exists.
4. Shell and navigation first (Abhi), then each owner adds their pages under `app/(dashboard)/<page>/`.
5. The release console must send the reason and the case `version` the approver saw; a stale version is refused by the database.
