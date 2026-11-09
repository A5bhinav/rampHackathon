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

test("a near miss marks the digits that differ, on the same formatting", async () => {
  const { digitMarks } = await import("../src/lib/risk.ts");
  const marks = digitMarks("+14155550142", "+14155550124");  // two adjacent digits swapped
  assert.equal(marks.map((m) => m.ch).join(""), "(415) 555-0142");
  assert.deepEqual(marks.filter((m) => m.differs).map((m) => m.ch), ["4", "2"]);
  assert.deepEqual(digitMarks("+14155550142", "+14155550142").filter((m) => m.differs), [], "same number: nothing marked");
  assert.deepEqual(digitMarks("+14155550142", null).filter((m) => m.differs), [], "nothing to compare with");
  assert.deepEqual(digitMarks("+442079460000", "+14155550142").filter((m) => m.differs), [], "different shapes: nothing marked");
  assert.deepEqual(digitMarks("+14155550142", "+14155550143").filter((m) => m.differs).map((m) => m.ch), ["2"], "one digit off");
});

test("shares are whole percents that add up to 100, zeros stay zero", async () => {
  const { shares } = await import("../src/lib/risk.ts");
  assert.deepEqual(shares([1, 1, 1]), [34, 33, 33]);
  assert.deepEqual(shares([0, 3, 0, 1]), [0, 75, 0, 25]);
  assert.deepEqual(shares([0, 0]), [0, 0]);
});

test("digits are marked only when the two numbers are a near miss of each other", async () => {
  const { digitMarks } = await import("../src/lib/risk.ts");
  // A vendor can be a near miss of another provable line than the one shown: then nothing is marked.
  assert.deepEqual(digitMarks("+14155550142", "+14155550199").filter((m) => m.differs), [], "two digits off, not swapped");
  assert.deepEqual(digitMarks("+14155550142", "+14155559876").filter((m) => m.differs), [], "a different number");
  assert.deepEqual(digitMarks("+14155550142", "+14155550214").filter((m) => m.differs), [], "a swap that isn't adjacent");
});
