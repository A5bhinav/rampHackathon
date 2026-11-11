// The two apps (spec 0063): each sidebar lists exactly its board's items, the switch keeps the
// dataset, and the old routes redirect to Control. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import config from "../next.config.ts";
import { appOf, canSeeControl, isCurrent, navFor, switchTargets, pageCrumb } from "../src/lib/apps.ts";

test("AP workspace lists Inbox, Cases, Vendors and Mail", () => {
  const nav = navFor("ap", "ds1");
  assert.deepEqual(nav.map((n) => [n.label, n.href]), [
    ["Inbox", "/d/ds1/cases"], ["Cases", "/d/ds1/cases/all"], ["Vendors", "/d/ds1/vendors"], ["Mail", "/d/ds1/mail"],
  ]);
  assert.ok(nav.every((n) => !n.group));
});

test("Control lists its four pages, then the Settings group", () => {
  const nav = navFor("control", "ds1");
  assert.deepEqual(nav.map((n) => [n.label, n.href, n.group ?? ""]), [
    ["Overview", "/d/ds1/control", ""],
    ["Trust Ledger", "/d/ds1/control/trust-ledger", ""],
    ["Evidence", "/d/ds1/control/evidence", ""],
    ["Assurance", "/d/ds1/control/assurance", ""],
    ["Rules & policy", "/d/ds1/control/rules", "Settings"],
    ["People", "/d/ds1/control/people", "Settings"],
    ["Integrations", "/d/ds1/control/integrations", "Settings"],
  ]);
});

test("the current item: Inbox owns case pages but not Cases; Overview owns only itself", () => {
  const [inbox, cases] = navFor("ap", "ds1");
  assert.ok(isCurrent(inbox, "/d/ds1/cases"));
  assert.ok(isCurrent(inbox, "/d/ds1/cases/c1/lineage"));
  assert.ok(!isCurrent(inbox, "/d/ds1/cases/all"));
  assert.ok(isCurrent(cases, "/d/ds1/cases/all"));
  const [overview, ledger] = navFor("control", "ds1");
  assert.ok(isCurrent(overview, "/d/ds1/control"));
  assert.ok(!isCurrent(overview, "/d/ds1/control/trust-ledger"));
  assert.ok(isCurrent(ledger, "/d/ds1/control/trust-ledger"));
});

test("the switch keeps the dataset, and each path belongs to one app", () => {
  assert.deepEqual(switchTargets("ds1"), { ap: "/d/ds1/cases", control: "/d/ds1/control" });
  assert.equal(appOf("/d/ds1/cases/c1"), "ap");
  assert.equal(appOf("/d/ds1/vendors"), "ap");
  assert.equal(appOf("/d/ds1/control/people"), "control");
});

test("Control is offered to everyone on demo data, and to approvers and auditors elsewhere", () => {
  assert.equal(canSeeControl(["clerk"], "demo"), true);
  assert.equal(canSeeControl(["clerk"], "dev"), false);
  assert.equal(canSeeControl([], "lab"), false);
  assert.equal(canSeeControl(["approver"], "dev"), true);
  assert.equal(canSeeControl(["auditor"], "lab"), true);
  assert.equal(canSeeControl(["admin"], "dev"), true);
});

test("the old routes redirect to Control, keeping the dataset (and the query, which Next passes on)", async () => {
  const rules = await config.redirects!();
  const to = (from: string) => rules.find((r) => r.source === from);
  assert.equal(to("/d/:datasetId/overview")?.destination, "/d/:datasetId/control");
  assert.equal(to("/d/:datasetId/risk")?.destination, "/d/:datasetId/control/trust-ledger");
  assert.equal(to("/d/:datasetId/evidence")?.destination, "/d/:datasetId/control/evidence");
  for (const r of rules) assert.equal(r.permanent, false, r.source);
});

test("the top bar names every page: the app, then the page", () => {
  assert.deepEqual(pageCrumb("control", "ds1", "/d/ds1/control/evidence"),
    { app: { label: "Control", href: "/d/ds1/control" }, page: "Evidence" });
  assert.equal(pageCrumb("control", "ds1", "/d/ds1/control").page, "Overview");
  assert.equal(pageCrumb("control", "ds1", "/d/ds1/control/people").page, "People");
  assert.equal(pageCrumb("control", "ds1", "/d/ds1/control/demo").page, "Demo controls");
  assert.equal(pageCrumb("ap", "ds1", "/d/ds1/cases").page, "Inbox");
  assert.equal(pageCrumb("ap", "ds1", "/d/ds1/cases/all").page, "Cases");
  assert.equal(pageCrumb("ap", "ds1", "/d/ds1/vendors").page, "Vendors");
  assert.equal(pageCrumb("ap", "ds1", "/d/ds1/ramp").page, "Ramp (mock)");
  assert.deepEqual(pageCrumb("ap", "ds1", "/d/ds1/somewhere"), { app: { label: "AP workspace", href: "/d/ds1/cases" }, page: null });
});
