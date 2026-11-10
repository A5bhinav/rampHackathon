# Frozen call-report findings (spec 0048, part B)

Each folder is one report `lab reports` found violating F1, F2 or F3, shrunk and frozen. `report.json`
holds the generated case field for field (the call's result and recorded answers, the voice service's
checks, Twilio's facts, the case's details and the generator's truth), `labels` (what failed when it was
frozen; never rewritten) and `why`. `lab reports` re-runs every one on every run, so a frozen finding stays
found until the product changes. A finding is never tuned away: it is triaged as a product defect or as a
check that is too strong (spec 0048, B6).

Frozen at seed 0 on Nov 10 from the reachable domain; all four are FINDINGS A10 (the company check
accepts a negated name, a reseller or a correction), owned by `company.py`'s owner, Abhi.
