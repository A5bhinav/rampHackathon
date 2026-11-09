# 0033: Stricter passwords

**Owner:** Abhi  **Slice:** money path (dashboard sign-up)  **Target day:** Sun Nov 8

## Goal

Self sign-up (spec 0027) accepted any password of 8 to 72 characters. Anyone with the link can
make an account, and approver accounts release payments, so passwords need more than length. The
team chose on Nov 8: keep the 8-character minimum, and require at least one lowercase letter, one
uppercase letter, one number and one symbol.

## Decisions

1. **Supabase enforces it** (done on Nov 8 through the management API):
   `password_required_characters` is set to lowercase, uppercase, digits and symbols, with
   `password_min_length` still 8. This also binds anyone who signs up through Supabase's API
   directly. Checked live: "Abcdefg1" and "abcdef1!" get `weak_password`; "Abcdef1!..." is accepted.
2. **The form checks the same rule first, and says it.** `cleanNewPassword` requires one character
   of each kind, and a symbol is exactly one of Supabase's (`PASSWORD_SYMBOLS`), so the form never
   passes a password Supabase would refuse. The rule is shown under the field and in the "weak"
   notice (`PASSWORD_RULE`).
3. **Existing passwords are unaffected.** Supabase checks the rule on sign-up and password change,
   not at sign-in.

## Contract touched

None.

## Acceptance

- [ ] A new password needs 8 to 72 characters with a lowercase letter, an uppercase letter, a digit and one of Supabase's symbols; anything else is refused before reaching Supabase.
- [ ] The rule is shown under the password field and in the "weak" notice.
- [ ] Supabase refuses a password without each kind (checked live, decision 1).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| the rule | `apps/web/tests/auth.test.ts` ("a new password is 8 to 72 characters with ...") |
| the words | `auth.test.ts` ("the password rule is spelled out for the form") |
| Supabase | checked live with throwaway sign-ups, deleted after (PR) |

## Out of scope

Leaked-password checks (Supabase's HIBP check needs a paid plan). Making existing users change
their password.

## Open questions

None.

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```
