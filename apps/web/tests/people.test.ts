// The admin's pages (spec 0095): role labels, who is offered them, and the activity log in words. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ACCESS_OFF, ROLES, isAdmin, logLine, roleLabels } from "../src/lib/people.ts";

const ada = { id: "00000000-0000-4000-8000-000000000001", display_name: "Ada Admin" };
const sam = { id: "00000000-0000-4000-8000-000000000002", display_name: "Sam Clerk" };
const entry = (type: string, detail: Record<string, unknown> = {}, actor: typeof ada | null = ada, subject: typeof sam | null = sam) =>
  ({ seq: 1, type, occurred_at: "2026-11-11T17:00:00.000000Z", actor, subject, detail, reason: "r" }) as never;

test("four roles, shown capitalised, admin offered the admin pages only when held", () => {
  assert.deepEqual(ROLES, ["clerk", "approver", "auditor", "admin"]);
  assert.equal(roleLabels(["clerk", "approver"]), "Clerk, Approver");
  assert.equal(roleLabels([]), "No role");
  assert.equal(isAdmin(["admin"]), true);
  assert.equal(isAdmin(["approver", "auditor"]), false, "an approver isn't offered admin pages");
});

test("the activity log says who did what to whom", () => {
  assert.equal(logLine(entry("role_granted", { role: "approver" })), "Ada Admin made Sam Clerk an Approver");
  assert.equal(logLine(entry("role_granted", { role: "clerk" })), "Ada Admin made Sam Clerk a Clerk");
  assert.equal(logLine(entry("role_granted", { role: "admin" })), "Ada Admin made Sam Clerk an Admin");
  assert.equal(logLine(entry("role_revoked", { role: "approver" })), "Ada Admin took the Approver role from Sam Clerk");
  assert.equal(logLine(entry("ramp_link_set", { ramp_user_id: "r1", previous_ramp_user_id: null })), "Ada Admin linked Sam Clerk to a Ramp user");
  assert.equal(logLine(entry("ramp_link_set", { ramp_user_id: null, previous_ramp_user_id: "r1" })), "Ada Admin cleared Sam Clerk's Ramp link");
  assert.equal(logLine(entry("access_disabled")), "Ada Admin turned off Sam Clerk's access");
  assert.equal(logLine(entry("demo_chaos", { enabled: true }, ada, null)), "Ada Admin turned chaos on");
  assert.equal(logLine(entry("role_granted", { role: "admin" }, null)), "The team (add_user.py) made Sam Clerk an Admin");
});

test("the access-off sentence is the core's, word for word", () => {
  assert.equal(ACCESS_OFF, "Your access is turned off. Ask an admin.");
});
