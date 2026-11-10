// The state labels every badge shows (spec 0041). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { STATE_LABEL, STATE_TONE, stateLabel } from "../src/lib/states.ts";

const ALL = ["open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked", "releasing", "released"];

test("every one of the ten states has a label and a tone", () => {
  assert.deepEqual(Object.keys(STATE_LABEL).sort(), [...ALL].sort());
  assert.deepEqual(Object.keys(STATE_TONE).sort(), [...ALL].sort());
  assert.equal(stateLabel("open"), "Placing hold");
  assert.equal(stateLabel("held"), "On hold");
  assert.equal(stateLabel("calling"), "Calling vendor");
  assert.equal(stateLabel("retry_wait"), "Retry scheduled");
  assert.equal(stateLabel("deciding"), "Deciding");
  assert.equal(stateLabel("awaiting_release"), "Ready to release");
  assert.equal(stateLabel("needs_review"), "Needs review");
  assert.equal(stateLabel("blocked"), "Blocked");
  assert.equal(stateLabel("releasing"), "Releasing hold");
  assert.equal(stateLabel("released"), "Released");
});

test("released reads as done and a scheduled retry as a warning", () => {
  assert.equal(STATE_TONE.released, "ok");
  assert.equal(STATE_TONE.retry_wait, "warn");
  assert.equal(STATE_TONE.blocked, "bad");
});

test("a state the dashboard doesn't know falls back to its words, never to a prototype key", () => {
  assert.equal(stateLabel("on_ice"), "on ice");
  assert.equal(stateLabel("constructor"), "constructor");
  assert.equal(stateLabel("toString"), "toString");
});
