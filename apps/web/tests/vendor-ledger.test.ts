// A vendor's history in the Trust Ledger (spec 0104): the page's pure helpers, and two guards on its source.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { hiddenLabel, kindLabel, ledgerHref, numberKinds } from "../src/lib/vendor-ledger.ts";

const example = (name: string) =>
  JSON.parse(readFileSync(new URL(`../../../packages/contracts/examples/selection.${name}.json`, import.meta.url), "utf8"));

test("each Trust Ledger row links to its vendor's page", () => {
  assert.equal(ledgerHref("ds1", "v1"), "/d/ds1/control/trust-ledger/v1");
});

test("numberKinds marks each number as the rule sees it today: chosen, alternate or refused", () => {
  assert.deepEqual([...numberKinds(example("picked"))],
    [["+14155550142", "pick"], ["+14155550117", "alternate"], ["+14155550143", "refused"]]);
  assert.ok([...numberKinds(example("no-provable-number")).values()].every((k) => k === "refused"));
});

test("document kinds and hidden text read as words", () => {
  assert.equal(kindLabel("vendor_master_record"), "Vendor master record");
  assert.equal(kindLabel("w9"), "W-9");
  assert.equal(kindLabel("portal_entry"), "Portal entry");
  assert.equal(hiddenLabel("visible"), null);
  assert.equal(hiddenLabel("white"), "hidden: white text");
  assert.equal(hiddenLabel("off_page"), "hidden: off the page");
  assert.equal(hiddenLabel("no_ink"), "hidden: no ink");  // a value a later migration adds reads as words too
});

const source = ["../src/app/d/[datasetId]/control/trust-ledger/[vendorId]/page.tsx", "../src/components/lineage.tsx"]
  .map((p) => readFileSync(new URL(p, import.meta.url), "utf8")).join("\n");

test("the page renders document text as plain text, never as HTML (R9)", () => {
  assert.ok(!source.includes("dangerouslySetInnerHTML"));
});

test("the page says provable or not provable, never safe", () => {
  assert.ok(!/\bsafe/i.test(source));
});
