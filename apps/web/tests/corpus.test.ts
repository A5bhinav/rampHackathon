// The corpus page (spec 0038): words for what the script recorded. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CORPUS, groups, numberVerdict, pickedPlanted, plantedStat, ruleResult } from "../src/lib/corpus.ts";

test("twenty cases: attacks, the stated limit, the controls, in that order", () => {
  const g = groups(CORPUS.cases);
  assert.deepEqual(g.map((x) => [x.kind, x.cases.length]), [["poison", 14], ["known_limit", 1], ["control", 5]]);
  assert.ok(CORPUS.cases.every((c) => c.matches), "the committed file says every case matches its truth");
});

test("a number's verdict in words", () => {
  const p = (eligible: boolean, reasons: string[] = []) => ({ e164: "+14155550142", eligible, reasons }) as never;
  assert.equal(numberVerdict(p(true), "+14155550142").word, "picked");
  assert.equal(numberVerdict(p(true), "+14155550100").word, "eligible, not picked");
  assert.equal(numberVerdict(p(false, ["near_miss_of_older_number"]), null).word, "refused");
  assert.deepEqual(numberVerdict({ ...(p(true) as object), truth: "poison" } as never, "+14155550142"), { word: "picked: the attacker's", tone: "bad" });
});

test("hidden text is all or some, and proposed cases are marked as such", () => {
  const p04a = CORPUS.cases.find((c) => c.id === "P04a")!;
  assert.ok(p04a.planted.some((p) => p.truth === "poison" && p.hidden === "all"));
  assert.ok(CORPUS.cases.some((c) => c.source === "proposed") && CORPUS.cases.some((c) => c.source === "named"));
});

test("the known limit is the one case where the rule picks a planted number", () => {
  const hits = CORPUS.cases.filter((c) => pickedPlanted(c));
  assert.deepEqual(hits.map((c) => c.id), ["K01"]);
});

test("what the rule did, in words, withheld included (spec 0079)", () => {
  assert.equal(ruleResult({ outcome: "no_provable_number", pick: null, reason: "x" }), "no provable number");
  assert.equal(ruleResult({ outcome: "withheld", pick: null, reason: "x" }), "withheld: a person verifies");
  assert.equal(ruleResult({ outcome: "picked", pick: "+14155550142", reason: null }), "(415) 555-0142");
});

test("the planted-pick stat: the known limit reads as one known limit, in amber, not a red 1 (spec 0066)", () => {
  assert.deepEqual(plantedStat(CORPUS.cases), { value: "1 known limit", tone: "warn" });
  const limit = CORPUS.cases.find((c) => c.kind === "known_limit")!;
  const attack = CORPUS.cases.find((c) => c.kind !== "known_limit" && !pickedPlanted(c))!;
  assert.deepEqual(plantedStat([attack]), { value: 0, tone: "ok" });
  // An attack case that picked a planted number is a failure, and says so in red.
  const failed = { ...attack, picked_planted: true } as typeof attack;
  assert.deepEqual(plantedStat([failed, limit]), { value: "1 + 1 known limit", tone: "bad" });
});
