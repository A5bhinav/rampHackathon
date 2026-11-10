# Frozen call-report findings (spec 0048, part B)

Each folder is one report `lab reports` found violating F1, F2 or F3, shrunk and frozen. `report.json`
holds the generated case field for field (the call's result and recorded answers, the voice service's
checks, Twilio's facts, the case's details and the generator's truth), `labels` (what failed when it was
frozen; never rewritten) and `why`. `lab reports` re-runs every one on every run, so a frozen finding stays
found until the product changes. A finding is never tuned away: it is triaged as a product defect or as a
check that is too strong (spec 0048, B6).

Frozen at seed 0 on Nov 10 from the reachable domain, all owned by `company.py`'s owner, Abhi: FINDINGS A10
(the company check accepts a negated name, a reseller, a correction or a past connection) and A8 (it accepts
another company whose name contains the vendor's). F1's labels name
the condition that failed (spec 0048 review fix 1): the first F1 case was frozen under the older, bare F1
label, which its `labels` field keeps as history.

`reports-f2-company-negation-helped-1151901e` was frozen under the first reachable domain, which let a
report skip a question. Production records every question in turn (spec 0048 review fix 4), so the move it
showed is no longer checked on it and it passes; its known-failures entry was taken off, the folder stays as
history, and `reports-f2-company-negation-helped-9192c077` is the same finding on a report production could send.
