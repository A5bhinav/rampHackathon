// The guided demo (spec 0039): steps and where each one goes. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { safeNext } from "../src/lib/story.ts";
import { TOUR, flowHref, tourAction, tourCase, tourHref, tourStep } from "../src/lib/tour.ts";

test("seven beats, in the demo's order, each with a caption (spec 0066)", () => {
  assert.deepEqual(TOUR.map((s) => s.title), ["The change", "Which number", "The call", "Code decides", "A second person", "Tamper", "The attack lab"]);
  assert.deepEqual(TOUR.map((s) => s.target), ["change", "number", "call", "decision", "second", "tamper", "lab"]);
  for (const s of TOUR) assert.ok(s.caption.length > 40, s.title);
  assert.equal(tourStep("2")?.target, "number");
  assert.equal(tourStep("7")?.target, "lab");
  for (const bad of ["0", "8", "9", "x", "1.5", "", "constructor"]) assert.equal(tourStep(bad), undefined, bad);
});

test("no caption sends people to a terminal, asks them to scroll, or says something isn't built", () => {
  for (const s of TOUR) {
    for (const banned of [/dialback-core/i, /dialback-ledger/i, /scroll/i, /isn't built/i, /not built/i]) assert.doesNotMatch(s.caption, banned, `${s.n}: ${banned}`);
  }
});

test("the card buttons: one per beat that has an action", () => {
  assert.deepEqual(TOUR.map((s) => s.action?.label ?? null),
    [null, "Open the call", "See the decision", "Try to release as the clerk", null, null, "Show the whole flow"]);
  assert.equal(tourAction(TOUR[1]), "/tour/3");
  assert.equal(tourAction(TOUR[2]), "/tour/4");
  assert.equal(tourAction(TOUR[3]), "/tour/5");
  assert.equal(tourAction(TOUR[6]), "/tour/flow");
  assert.equal(tourAction(TOUR[0]), null);
});

test("each beat opens the real page for the case the presenter's change opened", () => {
  const ids = { dataset: "ds1", vendor: "v1", kase: "c1" };
  assert.equal(tourHref(TOUR[0], ids), "/d/ds1/ramp/v1?tour=1");
  assert.equal(tourHref(TOUR[1], ids), "/d/ds1/cases/c1/lineage?tour=2");
  assert.equal(tourHref(TOUR[2], ids), "/d/ds1/cases/c1/call?tour=3");
  assert.equal(tourHref(TOUR[3], ids), "/d/ds1/cases/c1?tour=4#decision-h");
  assert.equal(tourHref(TOUR[4], ids), "/d/ds1/cases/c1?tour=5");
  assert.equal(tourHref(TOUR[5], ids), "/d/ds1/control/evidence?tour=6");
  assert.equal(tourHref(TOUR[6], ids), "/scorecard?tour=7");
  assert.equal(flowHref(ids), "/d/ds1/cases/c1/flow");
  // No change made yet: every case beat goes back to the change.
  const before = { dataset: "ds1", vendor: "v1", kase: null };
  for (const s of TOUR.slice(1, 5)) assert.equal(tourHref(s, before), "/d/ds1/ramp/v1?tour=1", s.title);
  assert.equal(flowHref(before), "/d/ds1/ramp/v1?tour=1");
  // No Acme in the Ramp mock at all: the vendor list.
  assert.equal(tourHref(TOUR[0], { dataset: "ds1", vendor: null, kase: null }), "/d/ds1/ramp?tour=1");
});

test("the case is the one the change opened: the vendor's newest, by the Ramp mock page's own rule", () => {
  const cases = [
    { id: "old", version: 1, opened_at: "2026-11-09T00:00:00Z", vendor: { id: "v1" } },
    { id: "new", version: 1, opened_at: "2026-11-10T09:00:00Z", vendor: { id: "v1" } },
    { id: "other", version: 1, opened_at: "2026-11-10T10:00:00Z", vendor: { id: "v2" } },
  ];
  assert.equal(tourCase(cases, "v1"), "new");
  assert.equal(tourCase(cases, "v3"), null);
  assert.equal(tourCase(cases, null), null);
});

test("after sign-in, a tour step is followed like a demo link", () => {
  assert.equal(safeNext("/tour/4"), "/tour/4");
  assert.equal(safeNext("/tour/flow"), "/tour/flow");
  assert.equal(safeNext("/tour/8"), "/tour/8", "an old link: the route sends it to step 7");
  assert.equal(safeNext("/tour/9"), "/app");
  assert.equal(safeNext("/tour/4/x"), "/app");
});

test("every step's link resolves to a page or a redirect (spec 0063)", async () => {
  const { existsSync, readdirSync } = await import("node:fs");
  const config = (await import("../next.config.ts")).default;
  const redirects = await config.redirects!();
  const app = new URL("../src/app/", import.meta.url);
  // Walk the app directory the way the router does: route groups are skipped, [x] matches any segment.
  const resolves = (dir: URL, segs: string[]): boolean => {
    const kids = existsSync(dir) ? readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory()) : [];
    if (segs.length === 0) return existsSync(new URL("page.tsx", dir)) || kids.some((k) => /^\(.+\)$/.test(k.name) && resolves(new URL(`${k.name}/`, dir), segs));
    const [head, ...rest] = segs;
    return kids.some((k) => (/^\(.+\)$/.test(k.name) && resolves(new URL(`${k.name}/`, dir), segs))
      || ((k.name === head || /^\[[^.]+\]$/.test(k.name)) && resolves(new URL(`${k.name}/`, dir), rest)));
  };
  const redirected = (path: string) => redirects.some((r) => new RegExp(`^${r.source.replace(/:\w+/g, "[^/]+")}$`).test(path));
  for (const ids of [{ dataset: "ds1", vendor: "v1", kase: "c1" }, { dataset: "ds1", vendor: "v1", kase: null }, { dataset: "ds1", vendor: null, kase: null }]) {
    for (const path of [...TOUR.map((s) => tourHref(s, ids)), ...TOUR.map(tourAction).filter((x): x is string => Boolean(x)), flowHref(ids)]) {
      const p = path.split(/[?#]/)[0];
      assert.ok(resolves(app, p.split("/").filter(Boolean)) || redirected(p), p);
    }
  }
});
