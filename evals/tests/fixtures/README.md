# evals/tests/fixtures

Recorded references for spec 0046's tests. Nothing here is read by the lab itself.

- `select-e3d57b5.py.txt`: `packages/ledger/src/dialback_ledger/select.py` exactly as it was at commit
  e3d57b5, the source `evals/redteam/breaks.json` pins (its sha256 is that file's `select_sha256`, and
  the tests check it). Kept as text so nothing imports or lints it by accident. The tests load it as a
  rule module, so they keep meaning the same thing after `select.py` is fixed, and in CI's shallow
  checkout, where that commit is absent.
- `labels-before-0046.json`: what `all_violations()` returned before spec 0046 exposed the probe
  outputs (the oracle as of 7ef40df, recorded from a checkout of 11e203d, Hypothesis 6.170.14), on that
  pinned rule: the 300 worlds `worldgen.worlds()` draws at seed 0 (`max_examples` 300, with pinned
  draws as `guided.pinned_draws()` makes them; each by its `world_digest` and labels), and the nine
  frozen cases then in `evals/corpus-generated`, with their worlds, so later additions or deletions
  there do not change it. Pinned draws matter: without them Hypothesis mixes in literals mined from
  whatever first-party modules are loaded, and pytest loads different ones than a script does.

`test_guided.py::test_exposing_probes_keeps_every_label` compares today's oracle with this record. If
it fails because the generator, the oracle's checks or Hypothesis changed on purpose, re-record it
from the commit before that change and say so in the commit message; never re-record to absorb a
label that changed by accident.
