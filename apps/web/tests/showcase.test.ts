// The landing page's product pictures (spec 0040): drawings of demo data that never break ADR-8. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { HERO_CASES, SHOWCASE, allText } from "../src/lib/showcase.ts";
import { STORY_STATIONS } from "../src/lib/story.ts";

test("every station has a picture, and the hero shows the three demo cases", () => {
  assert.deepEqual(Object.keys(SHOWCASE).sort(), STORY_STATIONS.map((s) => s.key).sort());
  assert.deepEqual(HERO_CASES.map((c) => c.vendor), ["Acme Fasteners LLC", "Brightline Office Supply Inc", "Cobalt Freight LLC"]);
});

test("no picture shows more than four account digits, or a phone number outside 555-01xx", () => {
  for (const text of allText()) {
    for (const run of text.replace(/\$[\d,]+\.\d\d/g, "$").replace(/\b[A-Z]+-\d+\b/g, "invoice").match(/\d[\d ().-]*\d/g) ?? []) {
      const digits = run.replace(/\D/g, "");
      if (digits.length <= 4) continue;
      if (/^\(?\d{3}\)?[ .-]?555[ .-]?01\d\d$/.test(run)) continue;  // a fictional phone number
      if (digits.length === 9 && /Routing \d{9}/.test(text)) continue;  // a labelled routing number
      assert.fail(`"${run}" in "${text}" looks like an account or phone number`);
    }
  }
});

test("the digits the vendor gives are a count, never digits", () => {
  const call = SHOWCASE.call;
  assert.ok(call.kind === "call");
  const last4 = call.turns.find((t) => t.slot === "account_last4");
  assert.equal(last4?.answer, "4 digits given");
});
