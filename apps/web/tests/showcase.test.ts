// The landing page's product pictures (spec 0040): drawings of demo data that match the seed and never break ADR-8. `pnpm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { HERO_CASES, SHOWCASE, allText } from "../src/lib/showcase.ts";
import { STORY_STATIONS } from "../src/lib/story.ts";

const root = new URL("../../../", import.meta.url);
const seed = JSON.parse(readFileSync(new URL("scripts/demo/seed.json", root), "utf8"));
const ledger = JSON.parse(readFileSync(new URL("packages/ledger/src/dialback_ledger/demo_seed.json", root), "utf8"));
const digits = (s: string) => s.replace(/\D/g, "");
const dollars = (cents: number) => `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

test("every station has a picture, and the hero shows the three demo cases", () => {
  assert.deepEqual(Object.keys(SHOWCASE).sort(), STORY_STATIONS.map((s) => s.key).sort());
  assert.deepEqual(HERO_CASES.map((c) => c.vendor), seed.vendors.map((v: { name: string }) => v.name));
});

test("the pictures match the demo seed", () => {
  const acme = seed.vendors[0];
  const live = acme.accounts.at(-1);  // the mock treats the last account listed as the active one
  const change = SHOWCASE.change;
  assert.ok(change.kind === "change");
  assert.equal(change.from, `Routing ${live.routing_number} · ending ${live.account_number.slice(-4)}`);
  assert.equal(change.to, `Routing ${seed.changes.acme.routing_number} · ending ${seed.changes.acme.account_number.slice(-4)}`);
  const hold = SHOWCASE.hold;
  assert.ok(hold.kind === "hold");
  assert.deepEqual(hold.bills.map((b) => [b.invoice, b.amount]),
    acme.bills.map((b: { invoice_number: string; amount_cents: number }) => [b.invoice_number, dollars(b.amount_cents)]));
  HERO_CASES.forEach((c, i) => {
    const bill = seed.vendors[i].bills[0];
    assert.deepEqual([c.next, c.amount], [bill.invoice_number, dollars(bill.amount_cents)]);
  });
  const number = SHOWCASE.number;
  assert.ok(number.kind === "number");
  assert.equal(digits(number.picked), digits(ledger.vendors.acme.phones.ar).slice(1), "the ledger picks Acme's AR line (test_demo.py)");
  assert.equal(digits(number.refused.number), digits(ledger.vendors.cobalt.phones.offered).slice(1), "Cobalt's request offers it");
});

test("no picture shows more than four account digits, or a phone number outside 555-01xx", () => {
  const labels = new Set(HERO_CASES.map((c) => c.next));
  for (const text of allText()) {
    const scrubbed = text.replace(/\$[\d,]+\.\d\d/g, "$").replace(/Routing \d{9}\b/g, "Routing").replace(/\b[A-Z]+-\d+\b/g, (m) => (labels.has(m) || /^ACME-\d{4}$/.test(m) ? "invoice" : m));
    for (const run of scrubbed.match(/\d[\d ().-]*\d/g) ?? []) {
      if (digits(run).length <= 4) continue;
      if (/^\(?\d{3}\)?[ .-]?555[ .-]?01\d\d$/.test(run)) continue;  // a fictional phone number
      assert.fail(`"${run}" in "${text}" looks like an account or phone number`);
    }
  }
});

test("the digits the vendor gives are a count, never digits", () => {
  const call = SHOWCASE.call;
  assert.ok(call.kind === "call");
  assert.equal(call.turns.find((t) => t.slot === "account_last4")?.answer, "4 digits given");
});

test("the page says the pictures are drawings", () => {
  const page = readFileSync(new URL("apps/web/src/app/page.tsx", root), "utf8");
  assert.match(page, /The pictures are drawings of the dashboard/);
});
