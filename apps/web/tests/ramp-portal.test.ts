// The demo's Ramp (mock) portal (spec 0077): the number hints the form gives, and how the page knows
// Dialback has picked a change up. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { accountValid, formatAccount, latestCase, markOf, parseMark, pickedUp, routingValid, watchFor } from "../src/lib/ramp-portal.ts";

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

const A = "3f6a9d2e-8b14-4c7a-a5e0-1d2c3b4a5f60";
const B = "0c9e8d7f-6a5b-4c3d-8e2f-1a0b9c8d7e6f";

test("the vendor's newest case, any state, is what a send is compared against", () => {
  const cases = [{ id: A, version: 2, opened_at: "2026-11-01T00:00:00Z", vendor: { id: "v1" } },
    { id: B, version: 1, opened_at: "2026-11-09T00:00:00Z", vendor: { id: "v1" } },
    { id: "x", version: 1, opened_at: "2026-11-10T00:00:00Z", vendor: { id: "v2" } }];
  assert.equal(latestCase(cases, "v1")?.id, B);
  assert.equal(latestCase(cases, "v3"), null);
  assert.equal(markOf({ id: A, version: 2 }), `${A}.2`);
  assert.deepEqual(parseMark(`${A}.2`), { id: A, version: 2 });
  assert.equal(parseMark("none"), null);
  assert.equal(parseMark("../x"), undefined);
  assert.equal(parseMark(undefined), undefined);
});

test("picked up: a new case, or the same case at a later version; never an earlier detection", () => {
  assert.equal(pickedUp(null, null), false);
  assert.equal(pickedUp({ id: A, version: 1 }, null), true, "the vendor's first case");
  assert.equal(pickedUp({ id: A, version: 1 }, { id: A, version: 1 }), false, "the same case, not yet changed again");
  assert.equal(pickedUp({ id: A, version: 2 }, { id: A, version: 1 }), true, "a new change on the open case");
  assert.equal(pickedUp({ id: B, version: 1 }, { id: A, version: 3 }), true, "a new case after a released one");
});

test("what the page shows after a send, and when it stops refreshing", () => {
  const sent = "2026-11-10T09:00:00Z";
  const t = (s: number) => Date.parse(sent) + s * 1000;
  assert.equal(watchFor(undefined, false, t(0)), "idle");
  assert.equal(watchFor("not a date", false, t(0)), "idle");
  assert.equal(watchFor(sent, false, t(30)), "detecting");
  assert.equal(watchFor(sent, true, t(30)), "opened");
  assert.equal(watchFor(sent, false, t(301)), "no_case", "five minutes on, it stops");
  assert.equal(watchFor(sent, true, t(3600)), "opened", "a released case still shows (and the page stops refreshing)");
});
