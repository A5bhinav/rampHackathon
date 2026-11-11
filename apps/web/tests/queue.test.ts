// The role-aware inbox (spec 0066, PR 3). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { canAct, needsYou, readPermissions, rowAction } from "../src/lib/queue.ts";
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

test("a row offers release only to someone who can approve, review to someone who can act; else Open", () => {
  assert.equal(rowAction("awaiting_release", perms({ can_approve: true, can_reject: true })), "Review and release");
  // The first of two approvers, or the reviewer who cleared it: they can only reject (spec review L2).
  assert.equal(rowAction("awaiting_release", perms({ can_reject: true })), "Review");
  assert.equal(rowAction("needs_review", perms({ can_review: true })), "Review");
  assert.equal(rowAction("awaiting_release", perms()), "Open");
  assert.equal(rowAction("needs_review", null), "Open");
  assert.equal(rowAction("blocked", perms({ can_approve: true })), "Open");
});

test("permissions are read for waiting cases only, at most a capped number, a few at a time; a failed read counts as none", async () => {
  const cases = [kase("a", "awaiting_release"), kase("b", "calling"), kase("c", "needs_review"), kase("d", "awaiting_release")];
  let inFlight = 0, most = 0;
  const read = async (id: string) => {
    inFlight++; most = Math.max(most, inFlight);
    await new Promise((r) => setTimeout(r, 5));
    inFlight--;
    if (id === "d") throw new Error("502");
    return perms({ can_approve: id === "a", can_review: id === "c" });
  };
  const got = await readPermissions(cases, read, { limit: 10, batch: 2 });
  assert.deepEqual([...got.keys()].sort(), ["a", "c", "d"]);
  assert.equal(got.get("d"), null, "a failed read: fail closed");
  assert.ok(most <= 2);
  assert.deepEqual(needsYou(cases, got).map((c: { id: string }) => c.id), ["a", "c"]);
  const capped = await readPermissions(cases, read, { limit: 1, batch: 2 });
  assert.deepEqual([...capped.keys()], ["a"], "past the cap: not read, so not counted");
});

test("the station strip names the approved count for what it is: waiting for approval", () => {
  const labels = stationCounts([], []).map((s) => s.label);
  assert.deepEqual(labels, ["Detected", "Held", "Number chosen", "Called", "Decided", "Waiting for approval", "Released"]);
});
