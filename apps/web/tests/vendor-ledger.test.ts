// A vendor's history in the Trust Ledger (spec 0104): the page's pure helpers, and two guards on its source.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import type { LedgerDocument } from "../src/lib/api-types.ts";
import { lanePoints, type RuleRun } from "../src/lib/lineage.ts";
import { hiddenLabel, kindLabel, laneDates, ledgerHref, numberKinds } from "../src/lib/vendor-ledger.ts";

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

// Spec 0104, D7. Acme in the demo seed: the vendor master record (Aug 2022) lists the main line; the
// contract (Mar 2023) lists the main and AR lines, and is where the rule first counts either of them.
const MAIN = "+12065550117", AR = "+12065550142";
const doc = (channel: LedgerDocument["custody_channel"], received_at: string, numbers: string[], visibility = "visible"): LedgerDocument => ({
  document_id: `${channel}-${received_at}`, kind: "other", custody_channel: channel, received_at, claimed_date: null, sender: null,
  subject: null, thread_id: null, parent_document_id: null,
  observations: numbers.map((e164) => ({ e164, role: "unknown", party: "vendor", visibility, quote: null })),
});
const FIRST = "2023-03-11T16:00:00.000000Z";
const rule: RuleRun = {
  rule_settings: { min_source_types: 2, min_age_days: 90, near_miss_distance: 1, same_subject_window_days: 30 },
  cutoff: "2026-11-11T00:00:00.000000Z", outcome: "picked", contact_point_id: "cp-ar",
  alternates: [{ contact_point_id: "cp-main", e164: MAIN, source_types: ["contract", "listing"] }],
  candidates: [
    { contact_point_id: "cp-ar", e164: AR, first_seen_at: FIRST, source_types: ["contract", "email"], on_file: false, eligible: true, rank: 1, reasons: [] },
    { contact_point_id: "cp-main", e164: MAIN, first_seen_at: FIRST, source_types: ["contract", "listing"], on_file: true, eligible: true, rank: 2, reasons: [] },
  ],
};
const documents = [
  doc("email", "2026-12-01T00:00:00.000000Z", [AR]),          // after the cutoff: never dates a dot
  doc("email", "2024-05-02T16:00:00.000000Z", [AR]),
  doc("listing", "2023-07-20T16:00:00.000000Z", [MAIN]),
  doc("contract", FIRST, [MAIN, AR]),
  doc("vendor_master", "2022-08-23T16:00:00.000000Z", [MAIN]),
  doc("email", "2021-01-05T16:00:00.000000Z", [AR], "white"),  // hidden text: never dates a dot
];
const at = (chart: ReturnType<typeof lanePoints>, e164: string, lane: string) =>
  chart!.points.find((p) => p.e164 === e164 && p.lane === lane)!;

test("each dot sits at the number's first sighting in that source: Acme's vendor master dot at its record's date", () => {
  const chart = lanePoints(rule, laneDates(documents, rule.cutoff));
  assert.equal(at(chart, MAIN, "on_file").at, "2022-08-23T16:00:00.000000Z");   // not the contract's date
  assert.equal(at(chart, MAIN, "contract").at, FIRST);
  assert.equal(at(chart, MAIN, "listing").at, "2023-07-20T16:00:00.000000Z");   // a later source is drawn later
  assert.equal(at(chart, AR, "email").at, "2024-05-02T16:00:00.000000Z");       // not the hidden or the late email
  assert.ok(at(chart, MAIN, "on_file").pct < at(chart, MAIN, "contract").pct);
  assert.equal(chart!.start, "2022-01-01");                                    // the axis reaches the record
});

test("a lane the documents don't date falls back to the number's first-seen date, as the case page draws it", () => {
  assert.ok(lanePoints(rule)!.points.every((p) => p.at === FIRST));
  const partial = lanePoints(rule, laneDates(documents.filter((d) => d.custody_channel !== "listing"), rule.cutoff));
  assert.equal(at(partial, MAIN, "listing").at, FIRST);
});

test("two numbers first seen in one document share a spot, and the chosen one is drawn last", () => {
  const chart = lanePoints(rule, laneDates(documents, rule.cutoff))!;
  assert.deepEqual(chart.points.filter((p) => p.lane === "contract").map((p) => [p.e164, p.kind]), [[MAIN, "alternate"], [AR, "pick"]]);
});
