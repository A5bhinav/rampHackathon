# Dialback

**Automate the callback.** AP teams are told to phone a vendor before paying a new bank
account, and they almost never do. Dialback holds payments to any vendor whose bank details
change in Ramp, picks a phone number it can prove belongs to the vendor, has a disclosed AI
agent make the call, lets code decide, and lets a second person release. Every step lands in
a hash-chained evidence log, and an attack lab replays hundreds of scams against the real
pipeline so the scorecard says how often it is fooled.

Built for the BBS x Ramp Hackathon ("Automate the Old Way") by Abhi, Inesh and Sami.
Design: [`docs/Dialback_System_Design.pdf`](docs/Dialback_System_Design.pdf). Rules every
person and agent works under: [`AGENTS.md`](AGENTS.md).

## Pick your part

Each folder below has a README with the job, the interfaces and contracts, how it works
inside, the data it owns, the rules that apply, your day-by-day plan, the tests you owe,
and where to start. Open the one assigned to you and begin.

| Part | Owner | Folder |
|---|---|---|
| Core service: Ramp adapter, detection, case worker, release | Abhi | [`apps/core/`](apps/core/README.md) |
| Ramp-shaped mock | Abhi | [`apps/ramp-mock/`](apps/ramp-mock/README.md) |
| Decision engine `decide()` | Abhi | [`packages/decision/`](packages/decision/README.md) |
| Evidence chain, Python side | Abhi | [`packages/evidence/`](packages/evidence/README.md) |
| Database: `app`, `audit`, `mock_ramp` schemas and the SQL functions | Abhi | [`supabase/`](supabase/README.md) |
| Trust Ledger: ingestion, extraction, number selection | Inesh | [`packages/ledger/`](packages/ledger/README.md) |
| Synthetic company, poison cases, frozen truth | Inesh | [`corpus/`](corpus/README.md) |
| Voice service: Twilio ConversationRelay adapter | Sami | [`apps/voice/`](apps/voice/README.md) |
| Dialogue policy, tools, speech guard | Sami | [`packages/policy/`](packages/policy/README.md) |
| Attack lab: runner, oracle, scorers, personas | Sami | [`evals/`](evals/README.md) |
| Dashboard, one page area each | all | [`apps/web/`](apps/web/README.md) |
| Contracts: OpenAPI, JSON Schema, examples, vectors | all | [`packages/contracts/`](packages/contracts/README.md) |
| Specs, one per feature | all | [`specs/`](specs/README.md) |
| Docs: ADRs, C4, sequences, threat model, state machine | all | [`docs/`](docs/README.md) |

The three slices meet only at the contracts, frozen on day 1, so everyone builds in parallel
with coding agents from the first hour.

## What runs today

The walking skeleton runs end to end: a bank change in the Ramp mock becomes a case, the
hold goes on, a call is placed and reported, code decides, a different person releases from
the dashboard, and the bills get their dates back. Inesh's `select_number` and Sami's voice
service are stood in for by labeled test doubles until theirs land (spec 0002, D10).

```bash
uv run python scripts/dev/stack.py --changes
```

Starts Postgres, the Ramp mock and the core with its worker, seeds a demo dataset with three
bank changes (one confirmed, one refused before dialing, one denied), and prints the
command to start the dashboard. `--smoke` instead runs the end-to-end checks and exits.

| Check | Command |
|---|---|
| Database: migrations, SQL tests, contracts, hash vectors | `scripts/db/test.sh` |
| Core, Ramp mock, decision engine | `cd apps/core && uv run pytest` (likewise `apps/ramp-mock`, `packages/decision`) |
| Dashboard | `cd apps/web && pnpm check:api && pnpm typecheck && pnpm build` |
| End to end | `uv run python scripts/dev/stack.py --smoke` |

`uv sync --all-packages` installs the Python workspace; `pnpm install` the dashboard. CI
runs all of these on every pull request (`.github/workflows/`).

## Dates

| | |
|---|---|
| Oct 31 | Contracts v0 merged |
| Nov 1 | Walking skeleton end to end (`specs/0001-walking-skeleton.md`) |
| Nov 4 | Go/no-go on the live call; rebalance work between slices |
| Nov 7 | Feature freeze (and the deck's Round 2 due date: confirm) |
| Nov 8 | Code freeze |
| Nov 10 | Submission by midday PT |
| Nov 19 | Live final at Ramp's San Francisco office |

## How we work

Small PRs to `main` at least once a day; `main` runs the demo every evening; a 15-minute
sync covers whether main is green, how the scorecard moved, pending contract PRs, and
blockers. Agents open PRs and never merge. The required checks are in
`.github/workflows/`; the review rules in `.github/CODEOWNERS` and
`.github/pull_request_template.md`.
