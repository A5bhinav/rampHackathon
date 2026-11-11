// The scorecard page (spec 0101): words and cells for what `lab snapshot` recorded. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { FUZZ_COLUMNS, LAB, declaredCount, ending, failures, fuzzCells, fuzzLegend, fuzzPaths, scenarioName, tile, voiceGroups, type LabScenario } from "../src/lib/lab.ts";

const s = (over: Partial<LabScenario>): LabScenario =>
  ({ id: "a02-lookalike-domain", class: "lookalike_domain", attack: true, outcome: "not_verified", passed: true, rule: "NV01_vendor_denied_change", declared: [], ...over });

test("the committed file is a whole run on a stated commit", () => {
  assert.match(LAB.commit, /^[0-9a-f]{7,12}$/);
  assert.equal(LAB.voice.scenarios.length, LAB.voice.total);
  assert.equal(LAB.voice.scenarios.filter((x) => x.passed).length, LAB.voice.passed);
  assert.ok(LAB.fuzz.worlds > 0);
  for (const worlds of Object.values(LAB.fuzz.hits)) assert.ok(worlds.every((w) => w >= 0 && w < LAB.fuzz.worlds));
});

test("each ending has its letter, word and tone; an unknown one says so", () => {  // L4
  assert.deepEqual(ending("verified"), { letter: "V", word: "Verified", tone: "ok" });
  assert.deepEqual(ending("needs_review"), { letter: "R", word: "Sent to a person", tone: "warn" });
  assert.deepEqual(ending("not_verified"), { letter: "B", word: "Blocked", tone: "info" });
  assert.deepEqual(ending("something_new"), { letter: "?", word: "something_new", tone: "muted" });
});

test("a scenario's name drops its number", () => {
  assert.equal(scenarioName("a02-lookalike-domain"), "lookalike domain");
  assert.equal(scenarioName("l16-garbage-answers"), "garbage answers");
  assert.equal(scenarioName("plain"), "plain");
});

test("a tile says how the call ended and whether it failed", () => {  // L4
  const passed = tile(s({}));
  assert.equal(passed.failed, false);
  assert.equal(passed.label, "lookalike domain: Blocked, by NV01_vendor_denied_change");
  const failed = tile(s({ outcome: "verified", passed: false, rule: null }));
  assert.equal(failed.failed, true);
  assert.equal(failed.label, "lookalike domain: Verified (failed: not what the lab required)");
});

test("a call resting on a declared fact is marked, in words and on the tile", () => {  // L9
  const t = tile(s({ outcome: "needs_review", rule: "NR21_line_risk", declared: ["line_risk"] }));
  assert.equal(t.declared, true);
  assert.equal(t.label, "lookalike domain: Sent to a person, by NR21_line_risk (rests on a fact the script supplies: line_risk)");
  assert.equal(tile(s({})).declared, false);
  assert.equal(declaredCount([s({}), s({ declared: ["direction"] })]), 1);
});

test("attacks come first, then legitimate calls", () => {  // L4
  const groups = voiceGroups([s({ id: "l01-x", attack: false }), s({ id: "a01-y" })]);
  assert.deepEqual(groups.map((g) => [g.key, g.items.map((x) => x.id)]), [["attack", ["a01-y"]], ["legit", ["l01-x"]]]);
});

test("each failure is listed in words", () => {  // L5
  const list = failures([s({}), s({ outcome: "verified", passed: false, rule: null }),
    s({ id: "l16-garbage-answers", attack: false, outcome: "verified", passed: false, rule: null })]);
  assert.deepEqual(list.map((f) => f.sentence), [
    "lookalike domain: this attack ended Verified.",
    "garbage answers: this legitimate-but-odd call ended Verified, not what the lab required.",
  ]);
  assert.equal(failures([s({})]).length, 0);
  // An attack that failed some other way doesn't claim it got through (review fix).
  assert.equal(failures([s({ outcome: "needs_review", passed: false })])[0].sentence,
    "lookalike domain: this attack ended Sent to a person, not what the lab required.");
});

test("the fuzz map has one cell per world, following its label", () => {  // L6
  const fuzz = { seed: 0, budget: 9, worlds: 5, hits: { M_incontext_label_moved_pick: [1, 3], M_shared_id_helped: [3, 4], M_new_thing: [0] } };
  assert.deepEqual(fuzzCells(fuzz), ["bad", "bad", "clean", "bad", "warn"]);   // the worst label wins; unknown is red
  assert.equal(fuzzCells({ ...fuzz, hits: {} }).length, 5);
});

test("the legend counts each label and the clean worlds", () => {  // L6
  const fuzz = { seed: 0, budget: 9, worlds: 5, hits: { M_incontext_label_moved_pick: [1, 3], M_shared_id_helped: [3, 4], M_new_thing: [0] } };
  const legend = fuzzLegend(fuzz);
  assert.deepEqual(legend.map((l) => [l.tone, l.count]), [["bad", 2], ["warn", 2], ["bad", 1], ["clean", 1]]);
  assert.match(legend[0].words, /could move the number/);
  assert.equal(legend[2].words, "M_new_thing");
});

test("the cells become one path per tone, in rows", () => {  // L6
  const { paths, rows } = fuzzPaths(["clean", "bad", "warn"], 2);
  assert.equal(rows, 2);
  assert.equal(paths.clean, "M0 0h9v9h-9z");
  assert.equal(paths.bad, "M10 0h9v9h-9z");
  assert.equal(paths.warn, "M0 10h9v9h-9z");
  assert.ok(FUZZ_COLUMNS > 0);
});
