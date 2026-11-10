# evals/corpus-generated: lab-found regressions (spec 0083)

Each folder is one world the attack lab found that the number-selection rule (`ledger-select-v1`,
`packages/ledger/src/dialback_ledger/select.py`) gets wrong. `world.json` holds the rule's inputs
field for field, `labels` says which lab check failed when it was frozen, and `why` explains it.

Every case here is re-run on each lab run (`evals/tests/test_generated_corpus.py` and `lab fuzz`).
A case passes when the lab's checks find nothing on it. While any case fails, the lab gate is red on
purpose: the fix belongs in the rule, not here. Do not delete a case to make the gate green; delete
it only after the rule is fixed and the case passes, or keep it as a regression.

The `M_*_helped` labels mean attacker evidence made a number eligible: the lab adds the evidence
(a second document under an existing id, a lookalike email) to the frozen world and re-runs the
rule. `M_incontext_label_moved_pick` (I12) means a label in the change context (a bank line, a fax
line, or a bare mention the reader can't place, which ingestion records as party `unknown`) moved
the call to a different number. Refusing the case is allowed; calling someone else is not. The lab
checks it both ways: it makes the context labels already in the world neutral, and it adds the
attacker's own label to the pick on each context path (the request email itself, a reply in its
thread from another domain or from the vendor's own, a same-subject email, a lookalike sender).

Which cases are reachable in production today: `planted-number-picked-after-in-thread-bank-label`
and `planted-number-picked-after-bare-mention-in-request` are (default settings, no shared ids, and
ingestion produces the label from ordinary text). The second is built by the ledger's real
`ingest_all`, and a test rebuilds it, so it cannot drift from what ingestion does. The
shared-id cases need two documents under one id, which `select_number` never passes, and
`near-miss-unanchored-by-old-lookalike` needs non-default settings, so those are latent.
`fuzz-seed0-m-incontext-label-moved-pick-29c233da` is the search's minimal world (one channel, no
minimum age); the same move happens at default settings, as the planted case shows.

`fuzz-seed0-m-incontext-label-blocked-7de35644` passes: it was frozen under I12's first wording
(no label may make a number ineligible) and has one eligible number, so refusing is allowed. It
stays as a regression for that boundary.

A case's `labels` field is never rewritten: it records the check that failed when the case was
frozen, under that check's name at the time. `why` carries what changed since, and `lab fuzz`
prints what fails today.

Read a case with:

```bash
cd evals && uv run python -c "from dialback_evals.gencorpus import load_world, rerun; print(rerun('corpus-generated'))"
```
