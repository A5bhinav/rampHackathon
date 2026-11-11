// The public bank-changes page's wording (spec 0089) is fixed; it must match exactly. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { BANK_CHANGES_TEXT } from "../src/lib/bank-changes.ts";

test("the page says exactly the approved wording", () => {
  assert.equal(
    BANK_CHANGES_TEXT,
    "We never change payment details because of an email. Every change is confirmed using contact details we already have for you, not ones sent in the request. To change your payment details, contact your account contact at the company you're writing to using details you already have for them. We will never ask you to confirm by replying to an email or calling a number someone sent you.",
  );
});
