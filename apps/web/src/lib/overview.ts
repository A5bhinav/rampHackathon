// The overview's counts (spec 0041), and the inbox's held totals. Display only: these count what
// the core sent and decide nothing.
import type { CaseState, HoldStatus } from "./api-types";
import { STATE_LABEL } from "./states.ts";

type Case = { id: string; state: string; hold_status: HoldStatus | null; bills_held: number; vendor: { id: string; legal_name: string } };

/** Cases per state, every known state listed (zeros included); unknown states are left out. */
export function countByState(cases: readonly Pick<Case, "state">[]): Record<CaseState, number> {
  const out = Object.fromEntries(Object.keys(STATE_LABEL).map((s) => [s, 0])) as Record<CaseState, number>;
  for (const c of cases) if (Object.hasOwn(out, c.state)) out[c.state as CaseState] += 1;
  return out;
}

export function holdsNotConfirmed(cases: readonly Pick<Case, "hold_status">[]): number {
  return cases.filter((c) => c.hold_status === "failed").length;
}

export function alertsBySeverity(alerts: readonly { severity: "info" | "warning" | "critical" }[]) {
  const out = { critical: 0, warning: 0, info: 0 };
  for (const a of alerts) if (Object.hasOwn(out, a.severity)) out[a.severity] += 1;
  return out;
}

const IN_PLACE: readonly (HoldStatus | null)[] = ["confirmed", "releasing"];

/** Held bills per case whose hold is in place, most bills first. */
export function heldByVendor(cases: readonly Case[]): { case_id: string; vendor: string; bills: number }[] {
  return cases.filter((c) => IN_PLACE.includes(c.hold_status) && c.bills_held > 0)
    .map((c) => ({ case_id: c.id, vendor: c.vendor.legal_name, bills: c.bills_held }))
    .sort((a, b) => b.bills - a.bills || a.vendor.localeCompare(b.vendor));
}

/** Bills on hold, and how many vendors they belong to. */
export function heldTotals(cases: readonly Case[]): { bills: number; vendors: number } {
  const rows = heldByVendor(cases);
  const vendors = new Set(cases.filter((c) => IN_PLACE.includes(c.hold_status) && c.bills_held > 0).map((c) => c.vendor.id));
  return { bills: rows.reduce((n, r) => n + r.bills, 0), vendors: vendors.size };
}
