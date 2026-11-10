// The guided demo (spec 0039): steps and where each one goes. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { safeNext } from "../src/lib/story.ts";
import { TOUR, tourHref, tourStep } from "../src/lib/tour.ts";

test("eight steps, in spec 0011's order, each with a caption", () => {
  assert.deepEqual(TOUR.map((s) => s.target), ["risk", "queue", "lineage", "call", "decision", "release", "evidence", "results"]);
  for (const s of TOUR) assert.ok(s.caption.length > 40, s.title);
  assert.equal(tourStep("3")?.target, "lineage");
  for (const bad of ["0", "9", "x", "1.5", "", "constructor"]) assert.equal(tourStep(bad), undefined, bad);
});

test("each step opens the real page with the tour carried along", () => {
  const ids = { dataset: "ds1", acme: "c1" };
  assert.equal(tourHref(TOUR[0], ids), "/d/ds1/risk?tour=1");
  assert.equal(tourHref(TOUR[2], ids), "/d/ds1/cases/c1/lineage?tour=3");
  assert.equal(tourHref(TOUR[4], ids), "/d/ds1/cases/c1?tour=5#decision-h");
  assert.equal(tourHref(TOUR[7], ids), "/corpus?tour=8");
  assert.equal(tourHref(TOUR[3], { dataset: "ds1", acme: null }), "/d/ds1/cases?tour=4&missing=acme", "no Acme case yet: the queue says why");
});

test("after sign-in, a tour step is followed like a demo link", () => {
  assert.equal(safeNext("/tour/4"), "/tour/4");
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
  for (const ids of [{ dataset: "ds1", acme: "c1" }, { dataset: "ds1", acme: null }]) {
    for (const s of TOUR) {
      const path = tourHref(s, ids).split(/[?#]/)[0];
      assert.ok(resolves(app, path.split("/").filter(Boolean)) || redirected(path), `${s.n}: ${path}`);
    }
  }
});
