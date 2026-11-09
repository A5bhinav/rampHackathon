// The money path drawn as a rail, and the held-payments chart's day axis (spec 0032).
// Display only: these count and place what the core sends; nothing here decides anything.
import type { CaseState } from "./api-types";
import { stationOf, type StageKey } from "./progress.ts";

export type Station = { key: StageKey; label: string; meaning: string };

/** The five stations every change passes through, each said in one plain line. */
export const STATIONS: Station[] = [
  { key: "hold", label: "Hold", meaning: "Payments to the vendor are frozen in Ramp" },
  { key: "call", label: "Call", meaning: "Dialback calls a number it can prove is theirs" },
  { key: "decision", label: "Decision", meaning: "Code checks the answers against the rules" },
  { key: "person", label: "Sign-off", meaning: "A different person approves or blocks" },
  { key: "release", label: "Release", meaning: "The hold lifts and saved dates come back" },
];

export type SignOffSplit = { needs_review: number; awaiting_release: number; blocked: number };
export type StationCount = Station & { count: number; split?: SignOffSplit };

/** How many changes sit at each station; released ones count at release. Sign-off is split
 *  by what it waits for. An unknown state counts at sign-off, like the case page shows it. */
export function stationCounts(cases: readonly { state: CaseState }[]): StationCount[] {
  const counts = [0, 0, 0, 0, 0];
  const split: SignOffSplit = { needs_review: 0, awaiting_release: 0, blocked: 0 };
  for (const c of cases) {
    counts[Math.min(stationOf(c.state).index, 4)] += 1;
    if (Object.hasOwn(split, c.state)) split[c.state as keyof SignOffSplit] += 1;
  }
  return STATIONS.map((s, i) => ({ ...s, count: counts[i], ...(s.key === "person" ? { split } : {}) }));
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
