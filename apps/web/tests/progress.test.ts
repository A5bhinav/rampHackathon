// The case page's progress strip: which stage a case is in, and which stages it really passed
// through, read from the evidence log for the current version only.
import assert from "node:assert/strict";
import { test } from "node:test";
import { progress } from "../src/lib/progress.ts";

const at = (...states: string[]) => states.map((to_state, i) => ({ to_state, version: 1, seq: i + 1 }));
const look = (steps: ReturnType<typeof progress>) => steps.map((s) => `${s.key}:${s.status}`);

test("a case waiting for release has passed through every stage before it", () => {
  const steps = progress("awaiting_release", 1, at("open", "held", "calling", "deciding", "awaiting_release"));
  assert.deepEqual(look(steps), ["held:done", "call:done", "decide:done", "person:current", "release:todo"]);
  assert.equal(steps[3]!.tone, "ok");
});

test("a stage the case jumped over is marked skipped, never done", () => {
  // no provable number: held straight to review, no call was made
  const steps = progress("needs_review", 1, at("open", "held", "needs_review"));
  assert.deepEqual(look(steps), ["held:done", "call:skipped", "decide:skipped", "person:current", "release:todo"]);
  assert.equal(steps[3]!.tone, "warn");
});

test("blocked is the person stage in red; a retry is the call stage in amber", () => {
  assert.equal(progress("blocked", 1, at("held", "needs_review", "blocked"))[3]!.tone, "bad");
  const retry = progress("retry_wait", 1, at("held", "calling", "retry_wait"));
  assert.deepEqual(look(retry).slice(0, 3), ["held:done", "call:current", "decide:todo"]);
  assert.equal(retry[1]!.tone, "warn");
});

test("released completes every stage it reached", () => {
  const steps = progress("released", 1, at("held", "calling", "deciding", "awaiting_release", "releasing", "released"));
  assert.deepEqual(look(steps), ["held:done", "call:done", "decide:done", "person:done", "release:current"]);
  assert.equal(steps[4]!.tone, "ok");
});

test("only the current version counts: a new change starts again from the hold", () => {
  const old = at("held", "calling", "deciding", "awaiting_release");
  const now = [...old, { to_state: "held", version: 2, seq: 5 }];
  assert.deepEqual(look(progress("held", 2, now)), ["held:current", "call:todo", "decide:todo", "person:todo", "release:todo"]);
});

test("each step's note is the current state in words", () => {
  const steps = progress("awaiting_release", 1, at("held", "calling", "deciding", "awaiting_release"));
  assert.equal(steps[3]!.note, "awaiting release");
  assert.equal(steps[0]!.note, undefined);
});
