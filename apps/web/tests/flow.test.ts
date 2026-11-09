// The rail's station counts and the held-payments chart's day window (spec 0032). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { STATIONS, dayWindow, place, stationCounts } from "../src/lib/flow.ts";

const at = (state: string) => ({ state }) as { state: Parameters<typeof stationCounts>[0][number]["state"] };

test("five stations, each with a plain meaning", () => {
  assert.deepEqual(STATIONS.map((s) => s.key), ["hold", "call", "decision", "person", "release"]);
  for (const s of STATIONS) assert.ok(s.meaning.length > 10, s.key);
});

test("each open change counts at its station; sign-off splits by what it waits for", () => {
  const counts = stationCounts([
    at("open"), at("held"), at("calling"), at("retry_wait"), at("deciding"),
    at("needs_review"), at("awaiting_release"), at("awaiting_release"), at("blocked"), at("releasing"), at("released"),
  ]);
  assert.deepEqual(counts.map((c) => c.count), [1, 3, 1, 4, 2]);
  assert.deepEqual(counts[3].split, { needs_review: 1, awaiting_release: 2, blocked: 1 });
});

test("an unknown state counts at sign-off, never at release", () => {
  const counts = stationCounts([at("mystery"), at("constructor")]);
  assert.deepEqual(counts.map((c) => c.count), [0, 0, 0, 2, 0]);
});

test("the window runs from a week before today to two weeks after, widened to fit, capped", () => {
  const now = new Date("2026-11-09T15:00:00Z");
  assert.deepEqual(dayWindow(now, []), { start: "2026-11-02", end: "2026-11-23", days: 21 });
  assert.deepEqual(dayWindow(now, ["2026-11-25"]), { start: "2026-11-02", end: "2026-11-26", days: 24 });
  assert.equal(dayWindow(now, ["2025-01-01", "2028-01-01"]).days, 60, "never wider than 60 days");
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
