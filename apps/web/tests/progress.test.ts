// The case page's progress strip (spec 0031). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { progress, visitedThisVersion, type ProgressInput } from "../src/lib/progress.ts";

function input(state: ProgressInput["state"], visited: ProgressInput["visited"], extra: Partial<ProgressInput> = {}): ProgressInput {
  return { state, visited, holdStatus: "confirmed", hasDecision: false, ...extra };
}

const kinds = (p: ReturnType<typeof progress>) => p.map((s) => s.status);
const DECIDED = ["open", "held", "calling", "deciding"] as const;

test("five stages in order, whatever the state", () => {
  const p = progress(input("open", ["open"], { holdStatus: "placing" }));
  assert.deepEqual(p.map((s) => s.key), ["hold", "call", "decision", "person", "release"]);
  assert.deepEqual(kinds(p), ["current", "todo", "todo", "todo", "todo"]);
});

test("a verified case waiting on an approver: the first three done, the person stage current in ok", () => {
  const p = progress(input("awaiting_release", [...DECIDED, "awaiting_release"], { hasDecision: true }));
  assert.deepEqual(kinds(p), ["done", "done", "done", "current", "todo"]);
  assert.equal(p[3].tone, "ok");
});

test("a decision of needs review: the decision is done, a person reviews", () => {
  const p = progress(input("needs_review", [...DECIDED, "needs_review"], { hasDecision: true }));
  assert.deepEqual(kinds(p), ["done", "done", "done", "current", "todo"]);
  assert.equal(p[3].tone, "warn");
});

test("no provable number: the call and the decision are skipped, a person reviews", () => {
  const p = progress(input("needs_review", ["open", "held", "needs_review"]));
  assert.deepEqual(kinds(p), ["done", "skipped", "skipped", "current", "todo"]);
  assert.equal(p[3].tone, "warn");
});

test("cleared by a reviewer with no call: still skipped, now waiting on an approver", () => {
  const p = progress(input("awaiting_release", ["open", "held", "needs_review", "awaiting_release"]));
  assert.deepEqual(kinds(p), ["done", "skipped", "skipped", "current", "todo"]);
});

test("a call that ended without answers is not done: it shows as not completed", () => {
  const p = progress(input("needs_review", ["open", "held", "calling", "needs_review"]));
  assert.deepEqual(kinds(p), ["done", "skipped", "skipped", "current", "todo"]);
  assert.equal(p[1].note, "Not completed");
  assert.equal(p[2].note, "Skipped");
});

test("a decision that failed to finish is not completed, not skipped", () => {
  const p = progress(input("needs_review", [...DECIDED, "needs_review"]));
  assert.deepEqual(kinds(p), ["done", "done", "skipped", "current", "todo"]);
  assert.equal(p[2].note, "Not completed");
});

test("a failed hold is marked failed, and the current stage still follows the state", () => {
  const review = progress(input("needs_review", ["open", "needs_review"], { holdStatus: "failed" }));
  assert.deepEqual(kinds(review), ["failed", "skipped", "skipped", "current", "todo"]);
  assert.equal(review[0].tone, "bad");
  assert.equal(review[0].note, "Not confirmed");
  // Cleared by a reviewer: the console offers approval, and the strip agrees.
  const cleared = progress(input("awaiting_release", ["open", "needs_review", "awaiting_release"], { holdStatus: "failed" }));
  assert.deepEqual(kinds(cleared), ["failed", "skipped", "skipped", "current", "todo"]);
});

test("a hold still being placed is current; one placed but unconfirmed past open is not ticked", () => {
  assert.deepEqual(kinds(progress(input("open", ["open"], { holdStatus: "placing" }))), ["current", "todo", "todo", "todo", "todo"]);
  const odd = progress(input("needs_review", ["open", "needs_review"], { holdStatus: null }));
  assert.equal(odd[0].status, "failed", "no hold record past open: never shown as done");
});

test("a call in progress and a retry are both the call stage", () => {
  assert.deepEqual(kinds(progress(input("calling", ["open", "held", "calling"]))), ["done", "current", "todo", "todo", "todo"]);
  assert.deepEqual(kinds(progress(input("retry_wait", ["open", "held", "calling", "retry_wait"]))), ["done", "current", "todo", "todo", "todo"]);
});

test("blocked after a decision is the person stage in the bad tone", () => {
  const blocked = progress(input("blocked", [...DECIDED, "blocked"], { hasDecision: true }));
  assert.deepEqual(kinds(blocked), ["done", "done", "done", "current", "todo"]);
  assert.equal(blocked[3].tone, "bad");
});

test("releasing is the release stage; only released ticks it", () => {
  const releasing = progress(input("releasing", [...DECIDED, "awaiting_release", "releasing"], { hasDecision: true, holdStatus: "releasing" }));
  assert.deepEqual(kinds(releasing), ["done", "done", "done", "done", "current"]);
  const released = progress(input("released", [...DECIDED, "awaiting_release", "releasing", "released"], { hasDecision: true, holdStatus: "released" }));
  assert.deepEqual(kinds(released), ["done", "done", "done", "done", "done"]);
});

test("an unknown state fails closed to the person stage, never to released", () => {
  for (const state of ["mystery", "constructor", "toString", "__proto__", "hasOwnProperty"]) {
    const p = progress(input(state as ProgressInput["state"], ["open", "held"]));
    assert.equal(p[3].status, "current", state);
    assert.equal(p[4].status, "todo", state);
  }
});

test("only this version's timeline counts: a new bank change starts the strip again", () => {
  const timeline = [
    { version: 1, to_state: "open" }, { version: 1, to_state: "held" }, { version: 1, to_state: "calling" },
    { version: 1, to_state: "deciding" }, { version: 2, to_state: "held" },
  ] as const;
  const visited = visitedThisVersion(timeline, 2);
  assert.deepEqual(visited, ["held"]);
  assert.deepEqual(kinds(progress(input("held", visited))), ["done", "current", "todo", "todo", "todo"]);
});
