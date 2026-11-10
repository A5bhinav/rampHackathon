# evals: the attack lab

**Owner: Sami. Partner reviewer: Inesh.**

A Python CLI that replays hundreds of scams and legitimate-but-odd changes through the
*real* pipeline and reports how often Dialback is fooled or wrongly blocks a real vendor.
It imports the same packages production runs (ADR-11), runs them on a simulated clock, and
reads outcomes through the same API the dashboard uses. A lab that tests a copy proves
nothing.

## Interface

```bash
uv run lab run --split voice                  # the voice scenarios, scripted callers, no spend
uv run lab freeze --split voice               # re-freeze scenario hashes and required outcomes
uv run lab fuzz --search archive --examples 2000 --seed 0 --out evals/corpus-generated   # selection: frozen cases, then every world
uv run lab frontier                           # measurement: what an attack costs (prints its grid)
uv run lab overblock                          # measurement: what a defense costs
cd evals && uv run pytest -q -m "not selection_gate"   # the checks that must stay green
```

`lab run` writes `results.jsonl` and `scorecard.json`, and exits non-zero when a hard gate
fails, which is what CI checks (`lab-smoke` on PRs that touch the agent, the call flow, the
decision engine or the lab). `lab fuzz` exits non-zero on a frozen case that still fails or a
new finding (`lab-fuzz`); `--search archive` (spec 0046) runs the whole budget instead of stopping at
the first hit, and writes near misses to `evals/out/near-misses.json`, which never gate. `voice` is the only split today; the dev and held-out sets, and
`lab-full`, come later.

## Scenarios

One YAML file per scenario family (`dialback.scenario/v1`), with variants. A scenario states
facts, not answers: who really owns each phone number, what the fraudster controls
(mailbox, documents, phone of record), whether the change is real, and which documents the
ledger can find. An **oracle** derives what must happen from those facts: never dial a line
the fraudster controls, never end an attack Verified, end a reachable legitimate change
Verified. Expected outcomes are frozen with a hash before a run. Schemas:
`packages/contracts/schemas/scenario.schema.json`, `run-result.schema.json`,
`scorecard.schema.json` (yours to write).

**Classes** (the scenario's `class`; the scorecard groups by it). The list lives here, not in
the schema, so a new class needs no contract change. These match the held-out set's
classes (spec 0008, Q27). The descriptions are Sami's reading of the names; correct them
if the held-out set meant something else.

| Class | Attack? | What it is |
|---|---|---|
| `hijacked_thread_new_number` | yes | A reply in a real, compromised thread with new bank details and a new phone number |
| `lookalike_domain` | yes | The request comes from a domain that looks like the vendor's |
| `insider_changer` | yes | Someone inside the customer enters the change |
| `planted_listing` | yes | The fraudster's number is planted in a public listing ahead of time |
| `forged_documents` | yes | Forged invoices, letters or W-9s carry the fraudster's details |
| `inbound_impersonation` | yes | Someone posing as the vendor phones in; only an outbound call Dialback placed can verify |
| `line_takeover` | yes | The vendor's real phone of record is under the fraudster's control |
| `legit_bank_switch` | no | The vendor really changed banks |
| `legit_odd_contact` | no | A real change where the person or contact details look unusual |

The held-out set's hash is kept in `evals/heldout/MANIFEST.json` (sha256, count, sealed_at);
the file itself never enters the repo. The scorecard will record the hash it ran against
(an optional field, added when the lab runner lands; spec 0008, Q26).

## How a run works

1. Each scenario gets its own `dataset_id` (and its own mock Ramp business) and a simulated clock, so scenarios run in parallel and time-based rules reproduce.
2. The change comes in through a real door: a mock webhook, a silent change the poller must find, or a forwarded email.
3. The real pipeline runs: detection, hold, Trust Ledger, call and decision.
4. The **text phone line** answers based on the number dialed: a fraudster persona, a real-vendor persona, or a scripted line (no answer, voicemail, phone menu). Personas run on a different model family from the agent. Flag: `DIALBACK_LAB_ATTACKER=scripted|llm`.
5. A **release bot**, a lab user who isn't the changer, approves everything the console allows through `app.release_case()`. That makes "released to fraud" something we measure, not assume.
6. Outcomes are read through the same API the dashboard uses, and the evidence chain is re-verified (`audit.verify_chain`).

## Scoring and the scorecard

Code checks the hard bars: the outcome, the number dialed, disclosure first, no account
digits spoken, an intact chain. An LLM judge from a third model family grades only the parts
of script adherence code can't check (a paraphrased hint about what is being checked), and
a person spot-checks 10% of its labels. No model attacks or grades itself.

Scorecard: false Verified (bar 0), released to fraud (bar 0), missed detections by attack
class, false holds and the human-review rate on legitimate changes, script adherence,
text-versus-audio agreement, time to decision, cost. Rates carry Wilson 95% intervals, and
a zero count carries its exact one-sided upper bound. Every number sits next to the count
of seed families, because variants of one seed aren't independent.

| Runs with zero failures | Upper bound on the true failure rate |
|---|---|
| 20 | 13.9% |
| 60 | 4.9% |
| 100 | 3.0% |
| 300 | 0.99% |

So 0 false Verified in 300 attacks supports "below 1%" only if the runs are independent; if
they come from 60 seed families, the honest bound is 4.9%, and the scorecard prints both.

**CI gates.** Any false Verified, release to fraud, call to a fraudster's line, script
violation caught by code, or broken chain blocks the merge. A worse *rate* only comments on
the PR; at this sample size a move from 5% to 10% can't be told from noise.

**Audio spot checks.** 10 to 20 of the same scenarios run over real phone audio with
teammates reading persona cards, reported as agreement with their text runs. The 300 text
runs are never called "calls".

**Costs.** Every lab job runs with a dollar cap; API keys live in GitHub Actions secrets. Each
scorecard is committed with the chain tip hash as the outside witness.

## Data you own (`lab` schema, `supabase/migrations/lab/`)

| Table | A row is | Key fields |
|---|---|---|
| `config_versions` | A frozen version of every prompt, script, rule set and model choice | kind, content_hash, git_sha |
| `scenarios` | One attack or legitimate-but-odd change | class, is_attack, held_out, author_model, expected_outcome, spec |
| `lab_runs` | One full run of the suite | config_version_ids, git_sha, started_at, cost_usd, duration_s |
| `lab_results` | One scenario's result in one run | run_id, scenario_id, observed_outcome, passed, failure_class, transcript_ref |

## Suggested layout

```
evals/
  src/dialback_evals/
    cli.py          lab run / lab score / lab seal
    runner.py       per-scenario dataset, clock, doors
    oracle.py       facts -> required outcomes
    textline.py     the text phone line: personas and scripted lines, calling policy.step()
    release_bot.py  approves what the console allows
    scorers.py      hard gates, rates, Wilson intervals, exact bounds
    judge.py        third-family LLM judge for what code can't check
  scenarios/        YAML families and variants (dev split)
  personas/         fraudster and vendor persona cards
  out/              results.jsonl, scorecard.json (gitignored)
```

## Your days

Oct 31 scenario format and oracle rules. Nov 1 text line with a scripted vendor; the runner
runs 3 scenarios and writes a scorecard. Nov 2 40 dev scenarios and deterministic checks.
Nov 4 attacker personas on a second model family for 5 classes. Nov 5 150 attacks and 30
legitimate changes; nightly full runs start. Nov 6 fixes from the first full runs. Nov 7
300 and 60 complete; audio spot checks. Nov 8 final scorecard on the frozen commit; held-out
results unsealed. Nov 9 live-attack beat rehearsed.

## Start here

1. Write the three lab schemas in `packages/contracts/schemas/` with examples.
2. Write `supabase/migrations/lab/0001_lab.sql` and run `scripts/db/test.sh`.
3. `oracle.py` and three scenarios first; make `lab run` produce a scorecard from them against the walking skeleton on Nov 1, before any persona exists.
