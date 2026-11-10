// The public "How it works" page's stations and the demo links (spec 0033). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEMO_STORIES, STORY_STATIONS, caseFor, demoStory, newestDemo, safeNext } from "../src/lib/story.ts";

test("seven stations in pipeline order, each saying what it stops and the rule it rests on", () => {
  assert.deepEqual(STORY_STATIONS.map((s) => s.key),
    ["change", "detect", "hold", "number", "call", "decide", "release"]);
  for (const s of STORY_STATIONS) {
    assert.ok(s.does.length > 20, s.key);
    assert.ok(s.stops.length > 20, s.key);
  }
  for (const s of STORY_STATIONS.slice(2)) assert.match(s.rules, /^R\d( and R\d)?$/, s.key);
});

test("three demo stories, one per seeded vendor, each with a ref and a legal name", () => {
  assert.deepEqual(DEMO_STORIES.map((s) => s.ref), ["acme", "brightline", "cobalt"]);
  assert.equal(demoStory("acme")?.legal_name, "Acme Fasteners LLC");
  assert.equal(demoStory("nope"), undefined);
  assert.equal(demoStory("constructor"), undefined);
});

const ds = (id: string, kind: string) => ({ id, kind, label: id, chain_id: "c", created_at: "2026-11-08T00:00:00Z", sim_now: null });

test("the newest demo dataset is the first demo one the core lists (newest first)", () => {
  assert.equal(newestDemo([ds("dev1", "dev"), ds("demo2", "demo"), ds("demo1", "demo")] as never)?.id, "demo2");
  assert.equal(newestDemo([ds("dev1", "dev"), ds("lab1", "lab")] as never), undefined);
  assert.equal(newestDemo([]), undefined);
});

type Row = { id: string; vendor: { id: string; legal_name: string }; opened_at: string };
const kase = (id: string, name: string, opened: string): Row => ({ id, vendor: { id: `v-${name}`, legal_name: name }, opened_at: opened });

test("the vendor's newest case, matched on its exact legal name", () => {
  const cases = [
    kase("old", "Acme Fasteners LLC", "2026-11-07T10:00:00Z"),
    kase("new", "Acme Fasteners LLC", "2026-11-08T10:00:00Z"),
    kase("other", "Acme Fasteners LLC (copy)", "2026-11-09T10:00:00Z"),
  ];
  assert.equal(caseFor(cases, "Acme Fasteners LLC")?.id, "new");
  assert.equal(caseFor(cases, "Cobalt Freight LLC"), undefined);
  assert.equal(caseFor([], "Acme Fasteners LLC"), undefined);
});

test("after sign-in, only a known demo link is followed; anything else opens the dashboard", () => {
  assert.equal(safeNext("/demo/acme"), "/demo/acme");
  for (const bad of [null, "", "/demo/nope", "/demo/acme/../../x", "//evil.example", "https://evil.example/demo/acme",
    "/demo/acme?x=1", "/d/123/cases", "/demo/constructor"]) assert.equal(safeNext(bad), "/app", String(bad));
});

test("a dataset page is a safe place to come back to after switching demo accounts", () => {
  const ds = "/d/0c9e8d7f-6a5b-4c3d-8e2f-1a0b9c8d7e6f";
  for (const ok of [ds, `${ds}/cases`, `${ds}/cases/3f6a9d2e-8b14-4c7a-a5e0-1d2c3b4a5f60/lineage`, `${ds}/evidence`]) assert.equal(safeNext(ok), ok, ok);
  for (const bad of [`${ds}//evil.example`, `${ds}/../x`, `${ds}/cases?x=1`, `${ds}/cases#x`, `/d/0c9e8d7f/cases`, `${ds}\\x`])
    assert.equal(safeNext(bad), "/app", bad);
});
