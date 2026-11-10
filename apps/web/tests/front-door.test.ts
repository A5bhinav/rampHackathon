// The front door (spec 0063): one screen, three ways in, seven stations. `pnpm test`.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { DOORS, STATION_WORDS } from "../src/lib/front-door.ts";

const src = (p: string) => new URL(`../src/${p}`, import.meta.url);

test("three doors: the demo, AP workspace and Control", () => {
  assert.deepEqual(DOORS.map((d) => [d.label, d.href]), [
    ["Watch the demo", "/tour/1"], ["AP workspace", "/app"], ["Control", "/app/control"],
  ]);
});

test("seven stations, one word each", () => {
  assert.deepEqual(STATION_WORDS.map((s) => s.word), ["Detected", "Held", "Number", "Called", "Decided", "Approved", "Released"]);
  for (const s of STATION_WORDS) assert.match(s.word, /^\S+$/);
});

test("the long page and its drawings are gone", () => {
  assert.equal(existsSync(src("components/showcase.tsx")), false);
  assert.equal(existsSync(src("lib/showcase.ts")), false);
  const page = readFileSync(src("app/page.tsx"), "utf8");
  for (const gone of ["SCAM", "COMPARE", "STORY_STATIONS", "DEMO_STORIES", "HeroShot", "What is real today"]) {
    assert.ok(!page.includes(gone), gone);
  }
});
