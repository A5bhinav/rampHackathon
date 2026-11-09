// The vendor risk list's pure helpers (spec 0026).
import assert from "node:assert/strict";
import { test } from "node:test";
import { counts, statusLabel, statusTone } from "../src/lib/risk.ts";

const row = (status: "near_miss" | "unproven" | "missing" | "provable") => ({ status });

test("each status has a label and a tone, worst in red", () => {
  assert.equal(statusLabel("near_miss"), "near miss");
  assert.equal(statusLabel("provable"), "provable");
  assert.equal(statusTone("near_miss"), "tone-bad");
  assert.equal(statusTone("unproven"), "tone-warn");
  assert.equal(statusTone("missing"), "tone-muted");
  assert.equal(statusTone("provable"), "tone-ok");
});

test("counts every status, in the API's worst-first order, zeros included", () => {
  assert.deepEqual(counts([row("provable"), row("near_miss"), row("provable")]),
    [["near_miss", 1], ["unproven", 0], ["missing", 0], ["provable", 2]]);
  assert.deepEqual(counts([]).map(([, n]) => n), [0, 0, 0, 0]);
});
