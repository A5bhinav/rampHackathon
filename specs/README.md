# specs: one short spec per feature, for the agents

One loop per task: write a spec here with an acceptance list, have the agent write failing
tests first, implement in a fresh session until they pass, review the diff against the spec
in another fresh session with the read-only reviewer agent, then open a small PR that lists
the commands run and their output.

Name files `NNNN-short-name.md`, numbered once and never reused. Copy `TEMPLATE.md`.
Keep a spec to one screen; if it needs more, it is two specs.

Each spec names: the owner, the contract it touches (and its PR, if any), the acceptance
list as checkboxes, the tests that prove each item, what is out of scope, and the open
questions an agent must not guess at.
