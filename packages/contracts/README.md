# packages/contracts: where the slices meet

**Owner: all three.** Every interface between owners is OpenAPI 3.1 or JSON Schema here,
frozen on day 1 (ADR-12), with generated TypeScript and Pydantic types and a Prism mock
server, so three people and their coding agents build in parallel against mocks from the
first hour and CI catches drift between spec and code.

## Rules

- A contract change goes in its own pull request, approved by both other teammates.
- From Nov 2, contracts only grow: new optional fields and new endpoints. A breaking change needs a five-minute call between all three.
- Generated code under `generated/` is never edited by hand; `.claude/settings.json` blocks it.
- Every schema ships with at least one example in `examples/`, named `<schema-stem>.<case>.json`, and CI validates every example in both languages.

## Layout

| Path | What | Owner | Status |
|---|---|---|---|
| `schemas/evidence-event.schema.json` | The envelope `audit.append_evidence()` hashes, with a body schema per event type | Abhi | done |
| `schemas/call-answers.schema.json` | The answer slots and their allowed values, shared by the agent's tools, the text line and `decide()`; referenced from `CallEndedBody.answers`. The root is the stored shape (account digits only as a keyed hash); `$defs/CallAnswersInTransit` carries the digits to `decide()` in memory | Sami | draft, in review |
| `schemas/selection.schema.json` | `select_number(case)` output: the chosen number, up to two alternates or `no_provable_number`, and every candidate with its reason | Inesh | to write |
| `schemas/scenario.schema.json`, `run-result.schema.json`, `scorecard.schema.json` | The attack lab's formats (`dialback.scenario/v1`) | Sami | to write |
| `openapi/dialback.yaml` | Dashboard to core: REST with the user's token | Abhi | to write |
| `openapi/internal.yaml` | Core to voice: `POST /calls`, then `call_ended` back | Abhi and Sami | draft, in review |
| `openapi/ramp-developer-api.json` | Ramp's own spec, committed for the mock and the adapter | Abhi | when obtained |
| `examples/` | One or more examples per schema | each | evidence-event done; call-answers draft |
| `vectors/evidence-chain.json` | Golden JCS strings and chain hashes that Python and SQL must both reproduce | Abhi | done |
| `generated/ts`, `generated/py` | Generated clients and models | CI | to set up |
| `scripts/validate.py` | Validates schemas and examples | | done |
| `scripts/make_vectors.py` | Regenerates the vectors (`--check` in CI) | | done |

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```

```bash
uv run --with rfc8785 python packages/contracts/scripts/make_vectors.py --check
```

## The evidence envelope, in short

Every case event becomes one envelope: `v`, `chain_id`, `dataset_id`, `case_id`, `event_id`,
`type`, `actor {kind, id}`, `occurred_at`, `idempotency_key`, `from_state`, `to_state`,
`version`, `body`. The database canonicalizes it with RFC 8785, hashes it, links it to the
previous event, and stores the exact canonical string. Numbers in a body must be integers.
Bodies never carry full bank account numbers, raw transcripts or recordings, only last-four
results and SHA-256 digests. Python code that builds or checks envelopes uses the
`rfc8785` package and the same two hash steps; `vectors/evidence-chain.json` is the proof
that both sides agree.

## Start here (for whichever contract you own)

1. Write the schema with `$schema` 2020-12, an `$id`, `additionalProperties: false` on objects you own, and a `description` on every field a teammate will read.
2. Add two examples: the common case and the edge case.
3. Run `validate.py`. Open a PR titled `contract: <name>` and request both teammates.
