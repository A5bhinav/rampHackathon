// The case page's progress strip (spec 0031). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { progress, type ProgressInput } from "../src/lib/progress.ts";

function input(state: ProgressInput["state"], visited: ProgressInput["visited"], extra: Partial<ProgressInput> = {}): ProgressInput {
  return { state, visited, holdStatus: "placed", hasDecision: false, ...extra };
}

const kinds = (p: ReturnType<typeof progress>) => p.map((s) => s.status);

test("five stages in order, whatever the state", () => {
  const p = progress(input("open", ["open"]));
  assert.deepEqual(p.map((s) => s.key), ["hold", "call", "decision", "person", "release"]);
  assert.deepEqual(kinds(p), ["current", "todo", "todo", "todo", "todo"]);
});

test("a verified case waiting on an approver: the first three done, the person stage current in ok", () => {
  const p = progress(input("awaiting_release", ["open", "held", "calling", "deciding", "awaiting_release"], { hasDecision: true }));
  assert.deepEqual(kinds(p), ["done", "done", "done", "current", "todo"]);
  assert.equal(p[3].tone, "ok");
});

test("no provable number: the call and the decision are skipped, a person reviews", () => {
  const p = progress(input("needs_review", ["open", "held", "needs_review"]));
  assert.deepEqual(kinds(p), ["done", "skipped", "skipped", "current", "todo"]);
  assert.equal(p[3].tone, "warn");
});

test("a call that ended without answers is not done: it shows as not completed", () => {
  const p = progress(input("needs_review", ["open", "held", "calling", "needs_review"]));
  assert.deepEqual(kinds(p), ["done", "skipped", "skipped", "current", "todo"]);
  assert.equal(p[1].note, "Not completed");
  assert.equal(p[2].note, "Skipped");
});

test("a failed hold shows the hold stage as current, in the bad tone", () => {
  const p = progress(input("needs_review", ["open", "needs_review"], { holdStatus: "failed" }));
  assert.deepEqual(kinds(p), ["current", "todo", "todo", "todo", "todo"]);
  assert.equal(p[0].tone, "bad");
});

test("a call in progress and a retry are both the call stage", () => {
  assert.deepEqual(kinds(progress(input("calling", ["open", "held", "calling"]))), ["done", "current", "todo", "todo", "todo"]);
  assert.deepEqual(kinds(progress(input("retry_wait", ["open", "held", "calling", "retry_wait"]))), ["done", "current", "todo", "todo", "todo"]);
});

test("blocked after a decision is the person stage in the bad tone; released is all done", () => {
  const blocked = progress(input("blocked", ["open", "held", "calling", "deciding", "blocked"], { hasDecision: true }));
  assert.deepEqual(kinds(blocked), ["done", "done", "done", "current", "todo"]);
  assert.equal(blocked[3].tone, "bad");
  const released = progress(input("released", ["open", "held", "calling", "deciding", "awaiting_release", "releasing", "released"], { hasDecision: true }));
  assert.deepEqual(kinds(released), ["done", "done", "done", "done", "done"]);
});

test("an unknown state fails closed to the person stage, never to released", () => {
  const p = progress(input("mystery" as ProgressInput["state"], ["open", "held"]));
  assert.equal(p[4].status, "todo");
  assert.equal(p[3].status, "current");
});
