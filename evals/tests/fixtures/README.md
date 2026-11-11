# evals/tests/fixtures

Recorded references for the lab's tests. Nothing here is read by the lab itself.

- `worldgen-first-cut.py.txt`: the first-cut world generator (commit cbc5b7e), for spec 0048 C3's test
  that it fails the coverage check.
- `select-e3d57b5.py.txt`: `packages/ledger/src/dialback_ledger/select.py` exactly as it was at commit
  e3d57b5 (tag `lab-0046-select-pin`), the rule spec 0046's planted breaks were written against. Its
  sha256 is `labels-before-0046.json`'s `select_sha256`, and the tests check it. Kept as text so
  nothing imports or lints it by accident. The tests load it as a rule module, so they keep meaning the
  same thing after `select.py` is fixed, and in CI's shallow checkout, where that commit is absent.
- `labels-before-0046.json`: what `all_violations()` returned before spec 0046 exposed the probe
  outputs, on that pinned rule: the 300 worlds `worldgen.worlds()` draws at seed 0 (`max_examples` 300,
  pinned draws through `dialback_evals.draws.sample`; each by its `world_digest` and labels), and the
  nine frozen cases in `evals/corpus-generated`, with their worlds, so later additions or deletions there
  do not change it. Re-recorded when the lab landed (lab landing PR 4), from the oracle and generator of
  the commit before the probes were exposed (6990fa4, the tip of PR 3), Hypothesis 6.170.14; two
  recordings were byte-identical. The first record (spec 0046's, from 11e203d) was made on an older
  generator and oracle, so its sample no longer matched.

`test_guided.py::test_exposing_probes_keeps_every_label` compares today's oracle with this record. If
it fails because the generator, the oracle's checks or Hypothesis changed on purpose, re-record it
from the commit before that change and say so in the commit message; never re-record to absorb a
label that changed by accident.
