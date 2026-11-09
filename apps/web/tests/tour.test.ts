// The guided demo (spec 0039): steps and where each one goes. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { safeNext } from "../src/lib/story.ts";
import { TOUR, tourHref, tourStep } from "../src/lib/tour.ts";

test("eight steps, in spec 0011's order, each with a caption", () => {
  assert.deepEqual(TOUR.map((s) => s.target), ["risk", "queue", "lineage", "call", "decision", "release", "evidence", "results"]);
  for (const s of TOUR) assert.ok(s.caption.length > 40, s.title);
  assert.equal(tourStep("3")?.target, "lineage");
  for (const bad of ["0", "9", "x", "1.5", "", "constructor"]) assert.equal(tourStep(bad), undefined, bad);
});

test("each step opens the real page with the tour carried along", () => {
  const ids = { dataset: "ds1", acme: "c1" };
  assert.equal(tourHref(TOUR[0], ids), "/d/ds1/risk?tour=1");
  assert.equal(tourHref(TOUR[2], ids), "/d/ds1/cases/c1/lineage?tour=3");
  assert.equal(tourHref(TOUR[4], ids), "/d/ds1/cases/c1?tour=5#decision-h");
  assert.equal(tourHref(TOUR[7], ids), "/corpus?tour=8");
  assert.equal(tourHref(TOUR[3], { dataset: "ds1", acme: null }), "/d/ds1/cases?tour=4&missing=acme", "no Acme case yet: the queue says why");
});

test("after sign-in, a tour step is followed like a demo link", () => {
  assert.equal(safeNext("/tour/4"), "/tour/4");
  assert.equal(safeNext("/tour/9"), "/app");
  assert.equal(safeNext("/tour/4/x"), "/app");
});
