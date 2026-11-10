// The seven stations (spec 0064), read from the current version's evidence events. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { REFRESH_STATES, STATIONS, refreshDelay, stationCounts, stations, stationsFromEvidence, type StationEvent } from "../src/lib/stations.ts";

let t = 0;
const ev = (type: string, to_state?: string): StationEvent => ({ type, to_state: to_state as StationEvent["to_state"], occurred_at: new Date(Date.UTC(2026, 10, 9, 14, t++)).toISOString(), seq: t });
const status = (s: ReturnType<typeof stations>) => s.map((x) => x.status[0]).join("");  // d c t b
const marker = (s: ReturnType<typeof stations>, key: string) => s.find((x) => x.key === key)?.marker;

const DETECTED = [ev("change_detected", "open")];
const HELD = [...DETECTED, ev("hold_confirmed", "held")];
const NUMBER = [...HELD, ev("number_resolved", "calling")];
const CALLED = [...NUMBER, ev("call_started", "calling"), ev("call_ended", "deciding")];
const DECIDED = [...CALLED, ev("decision_made", "awaiting_release")];
const APPROVED = [...DECIDED, ev("release_approved", "releasing")];
const RELEASED = [...APPROVED, ev("hold_release_confirmed", "released")];

test("seven stations in order, with their labels", () => {
  assert.deepEqual(STATIONS.map((s) => s.label), ["Detected", "Held", "Number chosen", "Called", "Decided", "Approved", "Released"]);
});

test("the happy path: the first station not yet done is current", () => {
  assert.equal(status(stations(DETECTED, "open", null)), "dcttttt");
  assert.equal(status(stations(HELD, "held", null)), "ddctttt");
  assert.equal(status(stations(NUMBER, "calling", null)), "dddcttt");
  assert.equal(status(stations([...NUMBER, ev("call_ended", "retry_wait")], "retry_wait", null)), "dddcttt");
  assert.equal(status(stations(CALLED, "deciding", null)), "ddddctt");
  assert.equal(status(stations(DECIDED, "awaiting_release", "verified")), "dddddct");
  assert.equal(status(stations(APPROVED, "releasing", "verified")), "ddddddc");
  assert.equal(status(stations(RELEASED, "released", "verified")), "ddddddd");
});

test("each done station carries its event's time", () => {
  const s = stations(DECIDED, "awaiting_release", "verified");
  assert.equal(s[1].at, HELD[1].occurred_at);
  assert.equal(s[4].at, DECIDED.at(-1)!.occurred_at);
  assert.equal(s[5].at, undefined);
});

test("the hold is never confirmed", () => {
  const s = stations([...DETECTED, ev("hold_failed", "needs_review")], "needs_review", null);
  assert.equal(status(s), "dbttttt");
  assert.equal(marker(s, "held"), "Hold not confirmed");
  assert.equal(s[1].tone, "bad");
});

test("no provable number", () => {
  const s = stations([...HELD, ev("no_provable_number", "needs_review")], "needs_review", null);
  assert.equal(status(s), "ddbtttt");
  assert.equal(marker(s, "number"), "No provable number");
  assert.equal(s[2].tone, "warn");
});

test("no person reached: tries used up, or the call never reported back", () => {
  for (const last of [ev("call_ended", "needs_review"), ev("call_timeout", "needs_review")]) {
    const s = stations([...NUMBER, ev("call_started", "calling"), last], "needs_review", null);
    assert.equal(status(s), "dddbttt", last.type);
    assert.equal(marker(s, "called"), "No person reached");
  }
});

test("decided: not verified stops the case; needs review waits on a person at Approved", () => {
  const nv = stations([...CALLED, ev("decision_made", "blocked")], "blocked", "not_verified");
  assert.equal(status(nv), "ddddbtt");
  assert.equal(marker(nv, "decided"), "Not verified");
  assert.equal(nv[4].tone, "bad");
  const nr = stations([...CALLED, ev("decision_made", "needs_review")], "needs_review", "needs_review");
  assert.equal(status(nr), "ddddbct");
  assert.equal(marker(nr, "decided"), "Needs review");
  assert.equal(nr[4].tone, "warn");
});

test("approved: one of two approvers, rejected, or blocked on review", () => {
  const co = stations([...DECIDED, ev("release_co_approved", "awaiting_release")], "awaiting_release", "verified");
  assert.equal(status(co), "dddddct");
  assert.equal(marker(co, "approved"), "1 of 2");
  const rej = stations([...DECIDED, ev("release_rejected", "blocked")], "blocked", "verified");
  assert.equal(status(rej), "dddddbt");
  assert.equal(marker(rej, "approved"), "Rejected");
  assert.equal(rej[5].tone, "bad");
  const rb = stations([...CALLED, ev("decision_made", "needs_review"), ev("review_blocked", "blocked")], "blocked", "needs_review");
  assert.equal(marker(rb, "approved"), "Rejected");
});

test("cleared on review after no provable number: the case waits at Approved", () => {
  const s = stations([...HELD, ev("no_provable_number", "needs_review"), ev("review_cleared", "awaiting_release")], "awaiting_release", null);
  assert.equal(s[2].status, "branched");
  assert.equal(s[5].status, "current");
});

test("a deadline or a failed command marks the station that was current", () => {
  const d = stations([...NUMBER, ev("deadline_reached", "needs_review")], "needs_review", null);
  assert.equal(status(d), "dddbttt");
  assert.equal(marker(d, "called"), "Deadline reached");
  const c = stations([...HELD, ev("command_failed", "needs_review")], "needs_review", null);
  assert.equal(marker(c, "number"), "A step could not finish");
  assert.equal(c[2].tone, "warn");
});

test("a second change voids the earlier version", () => {
  const s = stations([...DECIDED, ev("change_detected", "held"), ev("hold_confirmed", "held")], "held", null);
  assert.equal(status(s), "ddctttt");
});

test("an unknown state never shows as approved or released", () => {
  const s = stations(DECIDED, "on_ice" as never, "verified");
  assert.equal(s[5].status, "todo");
  assert.equal(s[6].status, "todo");
});

test("evidence rows: grouped by case, ordered by seq, and the call inferred without to_state", () => {
  const rows = [
    { seq: 9, case_id: "a", type: "decision_made", occurred_at: "2026-11-09T14:09:00Z" },
    { seq: 2, case_id: "a", type: "hold_confirmed", occurred_at: "2026-11-09T14:02:00Z" },
    { seq: 1, case_id: "a", type: "change_detected", occurred_at: "2026-11-09T14:01:00Z" },
    { seq: 3, case_id: "b", type: "change_detected", occurred_at: "2026-11-09T14:03:00Z" },
    { seq: 5, case_id: "a", type: "number_resolved", occurred_at: "2026-11-09T14:05:00Z" },
    { seq: 7, case_id: "a", type: "call_ended", occurred_at: "2026-11-09T14:07:00Z" },
    { seq: 8, case_id: "c", type: "call_ended", occurred_at: "2026-11-09T14:08:00Z" },
    { seq: 4, case_id: "c", type: "change_detected", occurred_at: "2026-11-09T14:04:00Z" },
    { seq: 6, case_id: "c", type: "hold_confirmed", occurred_at: "2026-11-09T14:06:00Z" },
    { seq: 6.5, case_id: "c", type: "number_resolved", occurred_at: "2026-11-09T14:06:30Z" },
  ];
  assert.equal(status(stationsFromEvidence(rows, { id: "a", state: "awaiting_release", outcome: "verified" })), "dddddct");
  assert.equal(status(stationsFromEvidence(rows, { id: "b", state: "open", outcome: null })), "dcttttt");
  const c = stationsFromEvidence(rows, { id: "c", state: "needs_review", outcome: null });
  assert.equal(marker(c, "called"), "No person reached");
});

test("with no rows for a case, its state alone places it", () => {
  assert.equal(status(stationsFromEvidence([], { id: "z", state: "calling", outcome: null })), "dddcttt");
  assert.equal(status(stationsFromEvidence([], { id: "z", state: "released", outcome: "verified" })), "ddddddd");
  assert.equal(status(stationsFromEvidence([], { id: "z", state: "awaiting_release", outcome: "verified" })), "dddddct");
});

test("station counts: each case at its current or branched station, released at the end", () => {
  const counts = stationCounts([
    { id: "a", state: "awaiting_release", outcome: "verified" },
    { id: "b", state: "calling", outcome: null },
    { id: "c", state: "released", outcome: "verified" },
    { id: "d", state: "blocked", outcome: "not_verified" },
  ], []);
  assert.deepEqual(counts.map((c) => c.count), [0, 0, 0, 1, 1, 1, 1]);
  const blocked = stationCounts([{ id: "d", state: "blocked", outcome: "not_verified" }], [
    { seq: 1, case_id: "d", type: "change_detected", occurred_at: "x" }, { seq: 2, case_id: "d", type: "decision_made", occurred_at: "y" },
  ]);
  assert.equal(blocked[4].count, 1);
  assert.deepEqual(blocked[4].markers, [{ label: "not verified", tone: "bad", count: 1 }]);
});

test("the case page refreshes only while code is working", () => {
  assert.deepEqual([...REFRESH_STATES].sort(), ["calling", "deciding", "held", "open", "releasing", "retry_wait"]);
});

// From the 0064 review: paths the first version drew wrongly.
test("a release that failed and went back to review voids the approval", () => {
  const failed = [...APPROVED, ev("command_failed", "needs_review")];
  const review = stations(failed, "needs_review", "verified");
  assert.equal(review[5].status, "branched");
  assert.equal(marker(review, "approved"), "A step could not finish");
  assert.equal(review[6].status, "todo");
  const cleared = stations([...failed, ev("review_cleared", "awaiting_release")], "awaiting_release", "verified");
  assert.equal(cleared[5].status, "current");
  assert.equal(cleared[5].at, undefined, "not stamped with the voided approval's time");
  const blocked = stations([...failed, ev("review_blocked", "blocked")], "blocked", "verified");
  assert.equal(marker(blocked, "approved"), "Rejected");
  assert.ok(blocked.every((s) => s.status !== "current"), "a blocked case is never drawn as releasing");
});

test("blocked or in review with nothing branched: no station is current", () => {
  assert.ok(stations(HELD, "needs_review", null).every((s) => s.status !== "current"));
  assert.ok(stations(HELD, "blocked", null).every((s) => s.status !== "current"));
});

test("one approval then the second: approved, and the marker is gone", () => {
  const s = stations([...DECIDED, ev("release_co_approved", "awaiting_release"), ev("release_approved", "releasing")], "releasing", "verified");
  assert.equal(s[5].status, "done");
  assert.equal(s[5].marker, undefined);
  assert.equal(s[6].status, "current");
});

test("an unknown state never shows approved or released, even with those events", () => {
  const s = stations(RELEASED, "on_ice" as never, "verified");
  assert.equal(s[5].status, "todo");
  assert.equal(s[6].status, "todo");
});

test("after a retry, the lists time Called from the call that led to the decision", () => {
  const rows = [
    { seq: 1, case_id: "a", type: "change_detected", occurred_at: "2026-11-09T14:01:00Z" },
    { seq: 2, case_id: "a", type: "hold_confirmed", occurred_at: "2026-11-09T14:02:00Z" },
    { seq: 3, case_id: "a", type: "number_resolved", occurred_at: "2026-11-09T14:03:00Z" },
    { seq: 4, case_id: "a", type: "call_ended", occurred_at: "2026-11-09T14:04:00Z" },
    { seq: 5, case_id: "a", type: "retry_timer_fired", occurred_at: "2026-11-09T15:00:00Z" },
    { seq: 6, case_id: "a", type: "call_ended", occurred_at: "2026-11-09T15:05:00Z" },
    { seq: 7, case_id: "a", type: "decision_made", occurred_at: "2026-11-09T15:06:00Z" },
  ];
  assert.equal(stationsFromEvidence(rows, { id: "a", state: "awaiting_release", outcome: "verified" })[3].at, "2026-11-09T15:05:00Z");
});

test("the state-only fallback claims nothing a null outcome or an unconfirmed hold doesn't say", () => {
  const cleared = stationsFromEvidence([], { id: "z", state: "awaiting_release", outcome: null, hold_status: "confirmed" });
  assert.equal(status(cleared), "ddtttct");
  const unheld = stationsFromEvidence([], { id: "z", state: "awaiting_release", outcome: null, hold_status: "failed" });
  assert.equal(unheld[1].status, "todo");
  assert.ok(stationsFromEvidence([], { id: "z", state: "needs_review", outcome: null }).every((s) => s.status !== "current"));
});

test("a case whose first rows aged out of the log keeps its earlier stations", () => {
  const rows = [
    { seq: 900, case_id: "a", type: "call_started", occurred_at: "2026-11-09T14:04:00Z" },
    { seq: 901, case_id: "a", type: "call_ended", occurred_at: "2026-11-09T14:05:00Z" },
  ];
  assert.equal(status(stationsFromEvidence(rows, { id: "a", state: "deciding", outcome: null, hold_status: "confirmed" })), "ddddctt");
});

test("the inbox counts a case where it waits, not at an earlier branch, and labels counts in words", () => {
  const counts = stationCounts([{ id: "a", state: "awaiting_release", outcome: "verified" }], [
    { seq: 1, case_id: "a", type: "change_detected" }, { seq: 2, case_id: "a", type: "hold_confirmed" },
    { seq: 3, case_id: "a", type: "no_provable_number" }, { seq: 4, case_id: "a", type: "review_cleared" },
  ]);
  assert.equal(counts[5].count, 1);
  const co = stationCounts([{ id: "b", state: "awaiting_release", outcome: "verified" }], [
    { seq: 1, case_id: "b", type: "change_detected" }, { seq: 2, case_id: "b", type: "decision_made" }, { seq: 3, case_id: "b", type: "release_co_approved" },
  ]);
  assert.deepEqual(co[5].markers, [{ label: "awaiting a second approver", tone: "warn", count: 1 }]);
});

test("the refresh backs off: every 2 seconds for the first minute, then every 10", () => {
  assert.equal(refreshDelay(0), 2000);
  assert.equal(refreshDelay(59_000), 2000);
  assert.equal(refreshDelay(61_000), 10_000);
});
