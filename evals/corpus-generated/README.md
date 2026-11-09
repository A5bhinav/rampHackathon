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
rule. Read a case with:

```bash
cd evals && uv run python -c "from dialback_evals.gencorpus import load_world, rerun; print(rerun('corpus-generated'))"
```
