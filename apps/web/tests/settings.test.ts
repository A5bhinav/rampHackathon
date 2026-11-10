// Control's Settings pages (spec 0063): what each can show today, and "not reported yet" for the rest. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { GUARANTEES, NOT_REPORTED, integrationRows, peopleRows, policyFrom } from "../src/lib/settings.ts";

test("R1 to R9, each with where it is enforced", () => {
  assert.deepEqual(GUARANTEES.map((g) => g.id), ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]);
  for (const g of GUARANTEES) {
    assert.ok(g.rule.length > 20, g.id);
    assert.ok(g.where.length > 10, g.id);
  }
});

test("integrations with no case data: Ramp and the chain from the API, the rest not reported", () => {
  const rows = integrationRows({ kind: "demo", chain: { ok: true, checked: 0 }, lastCheck: null, alerts: [] });
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.equal(by["Ramp"].value, "Ramp mock (demo dataset)");
  assert.equal(by["Evidence chain"].tone, "ok");
  assert.equal(by["Scheduled chain check"].value, "not run yet");
  assert.equal(by["Open alerts"].value, "none");
  assert.equal(by["Webhooks"].value, NOT_REPORTED);
  assert.equal(by["Voice service"].value, NOT_REPORTED);
  assert.equal(by["Webhooks"].tone, "muted");
});

test("integrations name a broken chain and count critical alerts", () => {
  const rows = integrationRows({
    kind: "lab", chain: { ok: false, checked: 4, first_bad_seq: 5 }, lastCheck: { finished_at: "2026-11-09T10:00:00Z", ok: false },
    alerts: [{ severity: "critical" }, { severity: "warning" }],
  });
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.equal(by["Evidence chain"].tone, "bad");
  assert.equal(by["Evidence chain"].value, "broken at row #5");
  assert.equal(by["Open alerts"].value, "1 critical, 1 warning");
  assert.equal(by["Open alerts"].tone, "bad");
});

test("people: the signed-in person, the demo accounts when demo sign-in is on, everyone else not reported", () => {
  const me = { user_id: "u1", display_name: "Riley Approver", roles: ["approver" as const] };
  assert.deepEqual(peopleRows(me, false).map((p) => p.name), ["Riley Approver"]);
  const demo = peopleRows(me, true);
  assert.deepEqual(demo.map((p) => p.name), ["Riley Approver", "Demo clerk", "Demo approver"]);
  assert.equal(demo[1].roles, "clerk");
});

test("rules & policy with no case: nothing invented", () => {
  const p = policyFrom(null, null);
  assert.equal(p.disclosure, null);
  assert.deepEqual(p.questions, []);
  assert.equal(p.rule, null);
  const q = policyFrom({ disclosure: "Hi", questions: [{ slot: "company_named", wording: "Who?" }], note: "n" },
    { rule_version: "ledger-select-v1", rule_settings: { min_source_types: 2, min_age_days: 90, near_miss_distance: 1, same_subject_window_days: 30 } });
  assert.equal(q.disclosure, "Hi");
  assert.equal(q.questions.length, 1);
  assert.equal(q.rule?.version, "ledger-select-v1");
});
