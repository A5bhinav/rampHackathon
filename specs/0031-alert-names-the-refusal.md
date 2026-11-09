# 0031: A refused call's alert names the reason

**Owner:** Abhi  **Slice:** money path (core API)  **Target day:** Sun Nov 8

## Goal

When the voice service refuses a call (a pre-dial check, spec 0012), the dashboard's alert banner
says "Command place_call failed after 1 attempts". That reads like a crash, but it is the system
working: no call, the hold stays on, a person reviews (R7). The banner should say why, in the
same words the case timeline already uses.

## Decisions

1. **The core rewrites the message when it serves alerts; the stored alert is unchanged.** The
   text is written by `app.apply_case_event` (0004). Changing it there means redefining the money
   path's SQL function on freeze day, and it would only fix new alerts. Reading it in the core
   fixes the alerts already raised too.
2. **The reason comes from the evidence.** For a `command_stuck` alert about `place_call`, the core
   reads the case's latest `command_failed` evidence for `place_call`. If its `last_error` is a
   pre-dial refusal, the message becomes "The call wasn't placed: <reason>. The hold stays on and a
   person must review." The reason uses the timeline's wording (`timeline.PRE_DIAL_REASON`), so the
   banner and the timeline say the same thing.
3. **Everything else is unchanged:** other alert kinds, other commands, and a `place_call` failure
   that isn't a refusal (the voice service unreachable) keep their stored text.
4. **The allowlist wording** drops "demo's": since spec 0030 the allowlist covers every dataset.

## Contract touched

None. `message` is already a free-text string.

## Acceptance

- [ ] A refused call's alert reads "The call wasn't placed: <reason>. The hold stays on and a person must review."
- [ ] The case timeline and the alert give the same reason.
- [ ] Other alerts, and a place_call failure that isn't a refusal, keep their stored message.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| refused call's alert, same reason as the timeline | `apps/core/tests/test_api.py::test_a_refused_calls_alert_names_the_reason` |
| others unchanged | `test_api.py::test_alerts_and_a_hand_edited_evidence_row` (existing), `apps/core/tests/test_timeline.py` or the same test |

## Out of scope

Changing the stored alert text in SQL.

## Open questions

None.

## Commands

```bash
cd apps/core && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
```
