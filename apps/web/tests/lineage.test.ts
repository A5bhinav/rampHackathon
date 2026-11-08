// The lineage panel's pure helpers (spec 0023), checked against the contract's own examples.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { phone, seenBefore, sourceLabel, split } from "../src/lib/lineage.ts";

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
