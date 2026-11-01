# The main flow: one bank change, end to end

The hold goes on before anything else happens, so a slow call, a crash or a confused
answer can only delay a payment, never send one. Every arrow below is an event through
`app.apply_case_event()` and a row in the evidence chain; see `../state-machine.md` for the
guards.

```mermaid
sequenceDiagram
    autonumber
    participant Ramp
    participant Core as Core service
    participant DB as Postgres (referee)
    participant Ledger as Trust Ledger
    participant Voice as Voice service
    participant Vendor
    participant Approver

    Ramp->>Core: vendors.updated webhook (signed) or poll diff
    Core->>DB: change_detected -> open; outbox: place_hold
    Core->>Ramp: save bill dates, place vendor hold, read back
    Core->>DB: hold_confirmed -> held; outbox: select_number
    Core->>Ledger: select_number(case)
    Ledger-->>Core: contact point with two independent sources, or none
    Core->>DB: number_resolved -> calling (or no_provable_number -> needs_review)
    Core->>Voice: POST /calls {case_id, attempt}
    Voice->>Vendor: disclosed, recorded call; three recording-only tools
    Voice->>Core: call_ended {result, answers}
    Core->>DB: call_ended -> deciding (or retry_wait / needs_review)
    Core->>Core: decide(facts) — pure function
    Core->>DB: decision_made -> awaiting_release | blocked | needs_review
    Approver->>DB: release_case() with their own token (not the changer)
    DB->>DB: release_approved -> releasing; outbox: release_hold
    Core->>Ramp: release hold, read back, restore bill dates
    Core->>DB: hold_release_confirmed -> released
```

Side paths: a second change while the case is unfinished sends it back to `held` with
`version + 1` and voids the decision; three business days with no decision, or a command
that keeps failing, sends it to `needs_review` with the hold still on; a vendor-supplied
change with no known changer needs two approvers.
