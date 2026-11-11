# evals/docs-generated: shrunk document findings (spec 0052)

Each folder is one plan the lab's document generator (`evals/src/dialback_evals/docgen.py`) found
failing a check, shrunk until removing any number, filler line or document, or simplifying any
phrasing or format, makes the failure go away. `case.json` holds:

- `plan`: the truth. Every number's owner (who really has it) and claim (the role and party the text
  gives it), with its phrasing, placement, format and visibility.
- `documents`: the plan rendered as the page model ingestion reads. A re-run checks the plan still
  renders to exactly these, so a case always means what it meant when it was frozen.
- `patterns`: what failed when it was frozen, as `<check>/<phrasing>/<placement>`.
- `violations`: each failure when it was frozen, with its pattern, its number and what happened. A G5
  pattern can name the phrasing of a rewording the plan itself doesn't hold; this says which.
- `why`: where it came from.

Only a run at the gate's seed (0) freezes cases here; another seed freezes into the gitignored
`evals/out/docs-generated-seed<N>`. `lab docs` re-runs every case here on each run, through the real `ingest_all` with the stub reader,
then `select()` for G7. A pattern a case shows must be on `lab-docs`' list in
`evals/known-failures.json` with its finding in `evals/FINDINGS.md`:

- **G3 and G4 on `label_number`**: the stub labels any role word before a colon the vendor's, whoever
  wrote it (A19).
- **G3, G4 and G5 on `clock_time`**: the words before a clock time's colon decide the label (A20). G5
  shows it as a flip: the same claim in another phrasing that names a bank comes back as the bank's.
- **G7**: a change-context mention of the vendor's real number that the stub doesn't label the
  vendor's (non-fax) line refuses it, and the call goes to the planted number (A1). G7's worlds are a
  sweep, two per phrasing and placement, so its 81 patterns are the same at every seed.

The fixes belong in `packages/ledger`, not here. When one lands, the listed patterns stop failing,
`lab docs` turns red, and the entries come off the list. Keep a case as a regression; delete it only
when what it tests no longer exists.
