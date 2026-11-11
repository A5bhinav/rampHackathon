// The role-aware inbox (spec 0066, PR 3). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { canAct, needsYou, rowAction } from "../src/lib/queue.ts";
import { stationCounts } from "../src/lib/stations.ts";

const perms = (o: Partial<{ can_approve: boolean; can_reject: boolean; can_review: boolean }> = {}) =>
  ({ can_approve: false, can_reject: false, can_review: false, reasons: [], ...o });

test("canAct is the lock card's rule: any console the core offers", () => {
  assert.equal(canAct(perms()), false);
  assert.equal(canAct(perms({ can_approve: true })), true);
  assert.equal(canAct(perms({ can_reject: true })), true);
  assert.equal(canAct(perms({ can_review: true })), true);
  assert.equal(canAct(null), false, "permissions that couldn't be read: fail closed");
});

const kase = (id: string, state: string) => ({ id, state }) as never;

test("Needs you counts only the waiting cases the signed-in person can act on", () => {
  const cases = [kase("a", "awaiting_release"), kase("b", "needs_review"), kase("c", "blocked"), kase("d", "calling")];
  // The clerk who entered every change: nothing to act on.
  const clerk = new Map([["a", perms()], ["b", perms()], ["c", perms()]]);
  assert.deepEqual(needsYou(cases, clerk).map((c: { id: string }) => c.id), []);
  // An approver: release a, review b; nobody acts on a blocked case.
  const approver = new Map([["a", perms({ can_approve: true, can_reject: true })], ["b", perms({ can_review: true })], ["c", perms()]]);
  assert.deepEqual(needsYou(cases, approver).map((c: { id: string }) => c.id), ["a", "b"]);
  assert.deepEqual(needsYou(cases, new Map()), [], "no permissions read: nothing counted");
});

test("a row offers release or review only to someone who can act; everyone else gets Open", () => {
  assert.equal(rowAction("awaiting_release", true), "Review and release");
  assert.equal(rowAction("needs_review", true), "Review");
  assert.equal(rowAction("awaiting_release", false), "Open");
  assert.equal(rowAction("needs_review", false), "Open");
  assert.equal(rowAction("blocked", true), "Open");
});

test("the station strip names the approved count for what it is: waiting for approval", () => {
  const labels = stationCounts([], []).map((s) => s.label);
  assert.deepEqual(labels, ["Detected", "Held", "Number chosen", "Called", "Decided", "Waiting for approval", "Released"]);
});
