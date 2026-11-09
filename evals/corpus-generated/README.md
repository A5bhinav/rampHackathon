# evals/corpus-generated: lab-found regressions (spec 0033)

Each folder is one world the attack lab found that the number-selection rule (`ledger-select-v1`,
`packages/ledger/src/dialback_ledger/select.py`) gets wrong. `world.json` holds the rule's inputs
field for field, `labels` says which lab check failed when it was frozen, and `why` explains it.

Every case here is re-run on each lab run (`evals/tests/test_generated_corpus.py` and `lab fuzz`).
A case passes when the lab's checks find nothing on it. While any case fails, the lab gate is red on
purpose: the fix belongs in the rule, not here. Do not delete a case to make the gate green; delete
it only after the rule is fixed and the case passes, or keep it as a regression.

The `M_*_helped` labels mean attacker evidence made a number eligible: the lab adds the evidence
(a second document under an existing id, a lookalike email) to the frozen world and re-runs the
rule. `M_incontext_label_blocked` (I12) means the opposite direction: a bank or fax label written in
the change context (the request's thread, its subject, a lookalike sender) made a number ineligible
that was first seen outside it, which lets the attacker move the call to a number they planted.

Which cases are reachable in production today: `planted-number-picked-after-in-thread-bank-label`
is (default settings, no shared ids, and ingestion produces the label from ordinary text). The
shared-id cases need two documents under one id, which `select_number` never passes, and
`near-miss-unanchored-by-old-lookalike` needs non-default settings, so those are latent.

Read a case with:

```bash
cd evals && uv run python -c "from dialback_evals.gencorpus import load_world, rerun; print(rerun('corpus-generated'))"
```
