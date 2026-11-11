// The overview's state machine (spec 0041), checked against docs/state-machine.md. `pnpm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { BRANCH, EDGES, MAIN, MAIN_EVENTS, PERSON_EVENTS, actionNotice } from "../src/lib/states.ts";

const ALL = ["open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked", "releasing", "released"];
const doc = readFileSync(new URL("../../../docs/state-machine.md", import.meta.url), "utf8");
// | From | Event | Who | Guard | To | Why |
const rows = doc.split("\n").filter((l) => /^\| [a-z(]/.test(l)).map((l) => l.split("|").map((c) => c.trim()))
  .map((c) => ({ from: c[1], event: c[2].replaceAll("`", ""), person: c[3].includes("person"), to: c[5] }));

test("the drawing has every case state exactly once", () => {
  const drawn = [...MAIN, ...BRANCH];
  assert.equal(drawn.length, new Set(drawn).size);
  assert.deepEqual([...drawn].sort(), [...ALL].sort());
});

test("the main row is the happy path, and each step on it is a move the database allows", () => {
  assert.deepEqual(MAIN, ["open", "held", "calling", "deciding", "awaiting_release", "releasing", "released"]);
  assert.equal(MAIN_EVENTS.length, MAIN.length - 1);
  MAIN_EVENTS.forEach((event, i) => {
    assert.ok(rows.some((r) => r.from === MAIN[i] && r.to === MAIN[i + 1] && r.event === event), `${MAIN[i]} -${event}-> ${MAIN[i + 1]}`);
  });
});

test("the person-only moves are the ones the doc marks person", () => {
  const fromDoc = [...new Set(rows.filter((r) => r.person).map((r) => r.event))].sort();
  assert.deepEqual([...PERSON_EVENTS].sort(), fromDoc);
});

test("every edge is copied from the doc", () => {
  const key = (e: { from: string; event: string; to: string }) => `${e.from} ${e.event} ${e.to}`;
  assert.deepEqual(EDGES.map(key).sort(), rows.filter((r) => r.from !== "(new)").map(key).sort());
  for (const e of EDGES) assert.equal(e.person, PERSON_EVENTS.includes(e.event), key(e));
});

test("a person's only ways out of blocked are the two dismissals of an email case (spec 0090)", () => {
  const moves = EDGES.filter((e) => e.from === "blocked" && e.person).map((e) => `${e.event} -> ${e.to}`).sort();
  assert.deepEqual(moves, ["release_approved -> releasing", "release_co_approved -> blocked"]);
});

// The case page's sentence after an action (the redirect's ?done= and ?to=). Final review, ledger rows 254
// and 283: a first dismissal (or a stale Approve click on a case that is now blocked) answers
// awaiting_second_approver and leaves the case blocked; the page said "release" and "moved to blocked".
test("a first dismissal reads as a dismissal and does not claim the case moved", () => {
  const text = actionNotice("awaiting_second_approver", "blocked");
  assert.equal(text, "The case is blocked, so your approval was recorded as a dismissal. A second, different approver must also dismiss it before the hold is lifted.");
  assert.equal(actionNotice("awaiting_second_approver", "awaiting_release"),
    "Your approval is recorded. A second approver must release this change. The case moved to ready to release.");
  assert.equal(actionNotice("applied", "releasing"), "Done. The case moved to releasing hold.");
  assert.equal(actionNotice("applied", undefined), "Done.");
  assert.equal(actionNotice("not_a_status", "blocked"), null);
  assert.equal(actionNotice(undefined, "blocked"), null);
});
