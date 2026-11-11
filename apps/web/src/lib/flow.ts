// The hold gate and the held-payments chart's day axis (spec 0032). The stations moved to
// lib/stations.ts (spec 0064).
// Display only: these count and place what the core sends; nothing here decides anything.
import type { HoldStatus } from "./api-types";

export type HoldGate = { kind: "held" | "lifted" | "placing" | "failed"; text: string };

/** What the payment path says about the hold, from the hold's own status. Only a failed hold is
 *  the alarm; one still being placed says so; an unknown status fails closed to "not confirmed". */
export function holdGate(status: HoldStatus | null): HoldGate {
  switch (status) {
    case "confirmed": return { kind: "held", text: "Held in Ramp" };
    case "releasing": return { kind: "held", text: "Held in Ramp, release under way" };
    case "released": return { kind: "lifted", text: "Hold lifted" };
    case "placing": case null: return { kind: "placing", text: "Placing the hold" };
    default: return { kind: "failed", text: "Hold not confirmed" };
  }
}

const HELD: readonly (HoldStatus | null)[] = ["confirmed", "releasing"];

/** The changes the held-payments chart draws: only those whose hold is in place (a failed or
 *  still-placing hold is not "held", and is counted instead), soonest payment first, at most
 *  `cap` of them. */
export function heldLanes<T extends { hold_status: HoldStatus | null; next_payment_date: string | null }>(cases: readonly T[], cap: number) {
  const held = cases.filter((c) => HELD.includes(c.hold_status) && c.next_payment_date)
    .sort((a, b) => a.next_payment_date!.localeCompare(b.next_payment_date!));
  return { shown: held.slice(0, cap), more: Math.max(0, held.length - cap), notHeld: cases.filter((c) => !HELD.includes(c.hold_status)).length };
}

const DAY_MS = 86_400_000;
const dayOf = (iso: string) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export type DayWindow = { start: string; end: string; days: number };

/** A week before today to two weeks after, widened a day past the dates it must show, never
 *  more than three weeks back or 60 days wide. Days are UTC, like every date on the dashboard. */
export function dayWindow(now: Date, dates: readonly string[]): DayWindow {
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const ms = dates.map(dayOf).filter(Number.isFinite);
  const start = Math.max(Math.min(today - 7 * DAY_MS, ...ms.map((d) => d - DAY_MS)), today - 21 * DAY_MS);
  const end = Math.min(Math.max(today + 14 * DAY_MS, ...ms.map((d) => d + DAY_MS)), start + 60 * DAY_MS);
  return { start: iso(start), end: iso(end), days: Math.round((end - start) / DAY_MS) };
}

/** Where a date (or a timestamp, by its UTC day) sits in the window, as a percent; a date
 *  outside it pins to the nearer edge and says which way it ran off. */
export function place(date: string | null | undefined, w: DayWindow): { pct: number; off: "before" | "after" | null } | null {
  if (!date) return null;
  const d = dayOf(date);
  const start = dayOf(w.start);
  const end = dayOf(w.end);
  if (d < start) return { pct: 0, off: "before" };
  if (d > end) return { pct: 100, off: "after" };
  return { pct: Math.round(((d - start) / (end - start)) * 1000) / 10, off: null };
}

