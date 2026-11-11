// The top bar's alert chip and the case page's own banners (spec 0066, PR 1). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { alertChip, alertsForCase } from "../src/lib/alerts.ts";

const a = (id: number, severity: "info" | "warning" | "critical", case_id: string | null = null) => ({ id, severity, case_id });

test("one chip for every open alert that isn't info, named by the worst severity", () => {
  assert.equal(alertChip([]), null);
  assert.equal(alertChip([a(1, "info")]), null, "info never makes a chip");
  assert.deepEqual(alertChip([a(1, "critical"), a(2, "critical", "c1")]), { label: "2 critical", tone: "bad", count: 2 });
  assert.deepEqual(alertChip([a(1, "warning")]), { label: "1 warning", tone: "warn", count: 1 });
  assert.deepEqual(alertChip([a(1, "critical"), a(2, "warning"), a(3, "info")]), { label: "1 critical · 1 warning", tone: "bad", count: 2 });
});

test("a case shows a full banner only for its own alerts, critical first", () => {
  const all = [a(1, "warning", "c1"), a(2, "critical", "c2"), a(3, "critical", "c1"), a(4, "info", "c1"), a(5, "critical")];
  assert.deepEqual(alertsForCase(all, "c1").map((x) => x.id), [3, 1]);
  assert.deepEqual(alertsForCase(all, "c3"), []);
});
