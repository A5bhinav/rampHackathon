// The hold gate and the held-payments chart's day window (spec 0032). The stations are tested in stations.test.ts (spec 0064). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { HoldStatus } from "../src/lib/api-types.ts";
import { dayWindow, heldLanes, holdGate, place } from "../src/lib/flow.ts";

type Lane = { id: string; hold_status: HoldStatus | null; next_payment_date: string | null };


test("the window runs from a week before today to two weeks after, widened to fit, capped", () => {
  const now = new Date("2026-11-09T15:00:00Z");
  assert.deepEqual(dayWindow(now, []), { start: "2026-11-02", end: "2026-11-23", days: 21 });
  assert.deepEqual(dayWindow(now, ["2026-11-25"]), { start: "2026-11-02", end: "2026-11-26", days: 24 });
  assert.equal(dayWindow(now, ["2025-01-01", "2028-01-01"]).days, 60, "never wider than 60 days");
  assert.equal(dayWindow(now, ["2025-01-01"]).start, "2026-10-19", "never more than three weeks back");
});

test("a date is placed by day; outside the window it pins to the edge and says which way", () => {
  const w = { start: "2026-11-02", end: "2026-11-23", days: 21 };
  assert.deepEqual(place("2026-11-02", w), { pct: 0, off: null });
  assert.deepEqual(place("2026-11-23", w), { pct: 100, off: null });
  assert.equal(place("2026-11-12T23:59:00Z", w)?.pct, place("2026-11-12", w)?.pct, "a timestamp counts by its UTC day");
  assert.deepEqual(place("2026-10-01", w), { pct: 0, off: "before" });
  assert.deepEqual(place("2027-01-01", w), { pct: 100, off: "after" });
  assert.equal(place(null, w), null);
});

test("the hold gate says what the hold's status says, and only a failed hold is the alarm", () => {
  assert.deepEqual(holdGate("confirmed"), { kind: "held", text: "Held in Ramp" });
  assert.deepEqual(holdGate("releasing"), { kind: "held", text: "Held in Ramp, release under way" });
  assert.deepEqual(holdGate("released"), { kind: "lifted", text: "Hold lifted" });
  assert.deepEqual(holdGate("failed"), { kind: "failed", text: "Hold not confirmed" });
  assert.deepEqual(holdGate("placing"), { kind: "placing", text: "Placing the hold" });
  assert.deepEqual(holdGate(null), { kind: "placing", text: "Placing the hold" });
  assert.deepEqual(holdGate("mystery" as never), { kind: "failed", text: "Hold not confirmed" }, "an unknown status fails closed");
});

test("the held-payments chart shows only changes whose hold is in place, soonest first, capped", () => {
  const c = (id: string, hold_status: string | null, next_payment_date: string | null) => ({ id, hold_status, next_payment_date }) as Lane;
  const cases = [c("a", "confirmed", "2026-11-12"), c("b", "failed", "2026-11-10"), c("c", "placing", "2026-11-09"),
    c("d", "releasing", "2026-11-11"), c("e", "confirmed", null)];
  const { shown, notHeld, more } = heldLanes(cases, 12);
  assert.deepEqual(shown.map((x) => x.id), ["d", "a"]);
  assert.equal(notHeld, 2, "failed and placing are left out, and counted");
  assert.equal(more, 0);
  const many = Array.from({ length: 15 }, (_, i) => c(`x${i}`, "confirmed", `2026-11-${String(10 + i).padStart(2, "0")}`));
  const capped = heldLanes(many, 12);
  assert.equal(capped.shown.length, 12);
  assert.equal(capped.more, 3);
  assert.equal(capped.shown[0].id, "x0");
});

