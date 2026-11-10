// The overview's counts (spec 0041), from fixture arrays. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { alertsBySeverity, countByState, heldByVendor, heldTotals, holdsNotConfirmed } from "../src/lib/overview.ts";

type C = Parameters<typeof heldByVendor>[0][number];
const kase = (id: string, state: string, hold: string | null, bills: number, vendor = id): C =>
  ({ id, state, hold_status: hold, bills_held: bills, vendor: { id: `v-${vendor}`, legal_name: vendor } }) as C;

const CASES = [
  kase("a", "awaiting_release", "confirmed", 2, "Acme"),
  kase("b", "needs_review", "confirmed", 1, "Brightline"),
  kase("c", "blocked", "confirmed", 3, "Cobalt"),
  kase("d", "open", "failed", 4, "Delta"),
  kase("e", "released", "released", 0, "Echo"),
  kase("f", "releasing", "releasing", 1, "Foxtrot"),
  kase("g", "held", "placing", 2, "Golf"),
];

test("cases count by state, zeros included, unknown states left out", () => {
  const n = countByState([...CASES, kase("x", "on_ice", null, 0)]);
  assert.equal(n.awaiting_release, 1);
  assert.equal(n.calling, 0);
  assert.equal(n.released, 1);
  assert.equal(Object.keys(n).length, 10);
});

test("holds not confirmed counts only a failed hold", () => {
  assert.equal(holdsNotConfirmed(CASES), 1);
  assert.equal(holdsNotConfirmed([]), 0);
});

test("alerts count by severity", () => {
  const a = (severity: "info" | "warning" | "critical") => ({ severity });
  assert.deepEqual(alertsBySeverity([a("critical"), a("warning"), a("critical"), a("info")]), { critical: 2, warning: 1, info: 1 });
  assert.deepEqual(alertsBySeverity([]), { critical: 0, warning: 0, info: 0 });
});

test("held bills by vendor: only holds in place, most bills first", () => {
  assert.deepEqual(heldByVendor(CASES).map((r) => [r.vendor, r.bills]), [["Cobalt", 3], ["Acme", 2], ["Brightline", 1], ["Foxtrot", 1]]);
});

test("held totals: bills on hold and the vendors they belong to", () => {
  assert.deepEqual(heldTotals(CASES), { bills: 7, vendors: 4 });
  assert.deepEqual(heldTotals([]), { bills: 0, vendors: 0 });
});
