// The lineage panel's pure helpers (spec 0023), checked against the contract's own examples.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { lanePoints, phone, seenBefore, sourceLabel, split } from "../src/lib/lineage.ts";

const example = (name: string) =>
  JSON.parse(readFileSync(new URL(`../../../packages/contracts/examples/selection.${name}.json`, import.meta.url), "utf8"));

test("split puts the pick first, then the alternates, then every refused number", () => {
  const s = split(example("picked"));
  assert.equal(s.pick?.e164, "+14155550142");
  assert.deepEqual(s.alternates.map((c) => c.e164), ["+14155550117"]);
  assert.deepEqual(s.refused.map((c) => c.e164), ["+14155550143"]);
});

test("with no provable number there is no pick and every number is refused", () => {
  const s = split(example("no-provable-number"));
  assert.equal(s.pick, null);
  assert.equal(s.alternates.length, 0);
  assert.equal(s.refused.length, 2);
});

test("phone formats US numbers and leaves others as they are", () => {
  assert.equal(phone("+14155550142"), "(415) 555-0142");
  assert.equal(phone("+442079460123"), "+442079460123");  // Ofcom's range for drama: fictional
});

test("seenBefore says how long before the cutoff a number was first seen", () => {
  const cutoff = "2026-11-03T16:05:31.000000Z";
  assert.equal(seenBefore("2023-02-14T18:20:03.000000Z", cutoff), "3 years before the request");
  assert.equal(seenBefore("2026-07-01T00:00:00Z", cutoff), "4 months before the request");
  assert.equal(seenBefore("2026-10-30T00:00:00Z", cutoff), "4 days before the request");
  assert.equal(seenBefore("2026-11-03T16:05:31.000000Z", cutoff), "with the request");
  assert.equal(seenBefore("2026-11-04T00:00:00Z", cutoff), "after the request");
});

test("source types read as words", () => {
  assert.equal(sourceLabel("vendor_portal"), "vendor portal");
  assert.equal(sourceLabel("listing"), "public listing");
  assert.equal(sourceLabel("something_new"), "something new");
});

test("lanePoints places every candidate in each of its source lanes, on file in its own lane", () => {
  const chart = lanePoints(example("picked"))!;
  assert.deepEqual(chart.lanes.map((l) => l.key), ["contract", "vendor_portal", "listing", "email", "on_file"]);
  assert.equal(chart.start, "2022-01-01");
  const of = (e164: string) => chart.points.filter((p) => p.e164 === e164);
  assert.deepEqual(of("+14155550142").map((p) => [p.lane, p.kind]).sort(), [["contract", "pick"], ["email", "pick"], ["listing", "pick"]]);
  assert.deepEqual(of("+14155550117").map((p) => [p.lane, p.kind]).sort(), [["email", "alternate"], ["on_file", "alternate"], ["vendor_portal", "alternate"]]);
  assert.deepEqual(of("+14155550143").map((p) => [p.lane, p.kind]), [["email", "refused"]]);
  for (const p of chart.points) assert.ok(p.pct >= 0 && p.pct <= 100, p.e164);
});

test("lanePoints puts the cutoff after the minimum-age line, and years in order", () => {
  const chart = lanePoints(example("picked"))!;
  assert.ok(chart.minAge.pct < chart.cutoffPct && chart.cutoffPct < 100);
  assert.equal(chart.minAge.days, 90);
  const refused = chart.points.find((p) => p.kind === "refused")!;
  assert.equal(refused.pct, chart.cutoffPct);  // first seen with the request
  assert.deepEqual(chart.ticks.map((t) => t.label), ["2022", "2023", "2024", "2025", "2026"]);
  assert.ok(chart.ticks.every((t, i) => i === 0 || t.pct > chart.ticks[i - 1].pct));
});

test("lanePoints with no candidates has nothing to draw", () => {
  assert.equal(lanePoints({ ...example("picked"), candidates: [] }), null);
  assert.equal(lanePoints(null), null);
});

// Spec 0104: the vendor page draws the rule as of now, which has no case, request or stored selection.
const asOfNow = (name: string) => {
  const { rule_version, rule_settings, requested_cutoff, cutoff, outcome, contact_point_id, alternates, candidates } = example(name);
  return { rule_version, rule_settings, as_of: requested_cutoff, cutoff, outcome, contact_point_id, alternates, candidates };
};

test("split and lanePoints take the rule as of now exactly as they take a stored selection", () => {
  for (const name of ["picked", "no-provable-number"]) {
    assert.deepEqual(split(asOfNow(name)), split(example(name)));
    assert.deepEqual(lanePoints(asOfNow(name)), lanePoints(example(name)));
  }
});

test("seenBefore names what the cutoff is", () => {
  const cutoff = "2026-11-03T16:05:31.000000Z";
  assert.equal(seenBefore("2023-02-14T18:20:03.000000Z", cutoff, "the cutoff"), "3 years before the cutoff");
  assert.equal(seenBefore(cutoff, cutoff, "the cutoff"), "with the cutoff");
  assert.equal(seenBefore("2026-11-04T00:00:00Z", cutoff, "the cutoff"), "after the cutoff");
});

// Two numbers first seen in one source on the same day used to share one spot, and the later dot
// hid the chosen one (Acme's Contract and Email rows on the vendor page).
test("lanePoints spreads dots that would cover each other, and draws the chosen dot last", () => {
  const s = example("picked");
  const pick = s.candidates[0];
  s.candidates[1] = { ...s.candidates[1], first_seen_at: pick.first_seen_at, source_types: ["contract", "email"] };
  const chart = lanePoints(s)!;
  for (const lane of ["contract", "email"]) {
    const here = chart.points.filter((p) => p.lane === lane);
    assert.deepEqual(here.map((p) => p.kind), ["alternate", "pick"], lane);   // drawn in this order: the pick on top
    assert.equal(here[0].pct, here[1].pct);
    assert.notEqual(here[0].lift, here[1].lift, `${lane}: the two dots sit at different heights`);
  }
  const alone = chart.points.filter((p) => p.lane === "listing");
  assert.deepEqual(alone.map((p) => p.lift), [0]);                             // a dot on its own stays centered
});
