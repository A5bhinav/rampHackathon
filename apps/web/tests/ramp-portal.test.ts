// The demo's Ramp (mock) portal (spec 0077): the number checks the form makes before the core makes
// them again, and how the page knows Dialback has picked the change up. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { accountValid, formatAccount, pickedUp, routingValid, watchFor } from "../src/lib/ramp-portal.ts";

test("routing numbers: nine digits with the ABA checksum", () => {
  for (const ok of ["026009593", "021000021", "121000248"]) assert.equal(routingValid(ok), true, ok);
  for (const bad of ["026009594", "02600959", "0260095930", "02600959a", "", " 026009593"]) assert.equal(routingValid(bad), false, bad);
});

test("account numbers: 4 to 17 digits", () => {
  for (const ok of ["0001", "987654321098", "12345678901234567"]) assert.equal(accountValid(ok), true, ok);
  for (const bad of ["123", "123456789012345678", "1234-5678", "12 34", ""]) assert.equal(accountValid(bad), false, bad);
});

test("an account shows as routing and last four only", () => {
  assert.equal(formatAccount({ routing_number: "026009593", last4: "0001" }), "026009593 ····0001");
  assert.equal(formatAccount(null), "No account on file");
});

const at = (iso: string, type = "change_detected") => ({ type, occurred_at: iso });

test("the change is picked up once the case's timeline has a change detected at or after it was sent", () => {
  const sent = "2026-11-10T09:00:00Z";
  assert.equal(pickedUp([at("2026-11-10T08:00:00Z")], sent), false, "an earlier change on the same case");
  assert.equal(pickedUp([at("2026-11-10T08:00:00Z"), at("2026-11-10T09:00:04Z")], sent), true);
  assert.equal(pickedUp([at("2026-11-10T08:59:50Z")], sent), true, "within the clock slack between the two servers");
  assert.equal(pickedUp([at("2026-11-10T09:00:04Z", "hold_confirmed")], sent), false, "only a detection counts");
  assert.equal(pickedUp([], sent), false);
});

test("what the page waits for after a send", () => {
  assert.equal(watchFor(null, null), "idle");
  assert.equal(watchFor("2026-11-10T09:00:00Z", null), "detecting");
  assert.equal(watchFor("2026-11-10T09:00:00Z", { picked: false }), "detecting");
  assert.equal(watchFor("2026-11-10T09:00:00Z", { picked: true }), "opened");
  assert.equal(watchFor("not a date", null), "idle");
});
