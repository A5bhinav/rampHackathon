// Pure helpers for the number lineage panel (spec 0023). No sentence here explains a decision:
// those come from the API's reason_text and the selection's own reason, written by the ledger.
import type { Candidate, Selection } from "./api-types";

export type Split = { pick: Candidate | null; alternates: Candidate[]; refused: Candidate[] };
/** What the chart and table need from a rule run: a stored selection, or the rule as of now (spec 0104). */
export type RuleRun = Pick<Selection, "candidates" | "cutoff" | "rule_settings" | "outcome" | "contact_point_id" | "alternates">;

/** The pick, then its alternates in rank order, then every number the rule refused. */
export function split(selection: RuleRun): Split {
  const ranked = selection.candidates.filter((c) => c.eligible).sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
  const alternateIds = new Set((selection.alternates ?? []).map((a) => a.contact_point_id));
  const pick = selection.outcome === "picked"
    ? selection.candidates.find((c) => c.contact_point_id === selection.contact_point_id) ?? null : null;
  return {
    pick,
    alternates: ranked.filter((c) => alternateIds.has(c.contact_point_id)),
    refused: selection.candidates.filter((c) => !c.eligible),
  };
}

/** +14155550142 -> (415) 555-0142; anything else as stored. */
export function phone(e164: string): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return m ? `(${m[1]}) ${m[2]}-${m[3]}` : e164;
}

const DAY_MS = 86_400_000;

/** How long before the cutoff a number was first seen, in the largest whole unit. */
export function seenBefore(firstSeen: string, cutoff: string, what = "the request"): string {
  const ms = Date.parse(cutoff) - Date.parse(firstSeen);
  const days = Math.floor(ms / DAY_MS);
  if (ms < 0) return `after ${what}`;
  if (days < 1) return `with ${what}`;
  const [n, unit] = days >= 365 ? [Math.floor(days / 365), "year"] : days >= 60 ? [Math.floor(days / 30), "month"] : [days, "day"];
  return `${n} ${unit}${n === 1 ? "" : "s"} before ${what}`;
}

const SOURCES: Record<string, string> = {
  email: "email", contract: "stored contract", vendor_portal: "vendor portal", listing: "public listing", paper_mail: "paper mail",
};

export function sourceLabel(source: string): string {
  return SOURCES[source] ?? source.replaceAll("_", " ");
}

// The lineage chart (spec 0041): one dot per candidate per source type, on a time axis from the
// January before the earliest sighting to just past the cutoff. A dot sits at the date `dates` gives
// for that number and lane (the vendor page's per-source dates, spec 0104 D7), else at the number's
// overall first-seen date (the case page: the selection has no per-source dates, spec 0104 Q224).
const LANE_ORDER: string[] = ["contract", "vendor_portal", "listing", "email", "paper_mail"];
const LANE_NOTE: Record<string, string> = { email: "counts once", on_file: "never counts" };

export type LanePoint = { lane: string; e164: string; kind: "pick" | "alternate" | "refused"; pct: number; at: string };
export type LaneChart = {
  start: string; end: string;
  lanes: { key: string; label: string; note?: string }[];
  points: LanePoint[];
  ticks: { label: string; pct: number }[];
  cutoffPct: number;
  minAge: { pct: number; days: number };
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export const laneKey = (e164: string, lane: string) => `${e164} ${lane}`;
const DRAW = { refused: 0, alternate: 1, pick: 2 } as const;

export function lanePoints(selection: RuleRun | null, dates?: Map<string, string>): LaneChart | null {
  if (!selection || selection.candidates.length === 0) return null;
  const pickId = split(selection).pick?.contact_point_id;
  const cutoff = Date.parse(selection.cutoff);
  const dots = selection.candidates.flatMap((c) => {
    const kind: LanePoint["kind"] = c.contact_point_id === pickId ? "pick" : c.eligible ? "alternate" : "refused";
    return [...c.source_types, ...(c.on_file ? ["on_file"] : [])]
      .map((lane) => ({ lane, e164: c.e164, kind, at: dates?.get(laneKey(c.e164, lane)) ?? c.first_seen_at }));
  }).sort((a, b) => DRAW[a.kind] - DRAW[b.kind]);  // drawn in this order: on a shared spot the chosen dot shows
  const seen = [...selection.candidates.map((c) => c.first_seen_at), ...dots.map((d) => d.at)].map(Date.parse).filter(Number.isFinite);
  const firstYear = new Date(Math.min(cutoff, ...seen)).getUTCFullYear();
  const start = Date.UTC(firstYear, 0, 1);
  const end = cutoff + Math.max((cutoff - start) * 0.025, 21 * DAY_MS);  // a sliver past the request
  const pct = (ms: number) => Math.round(Math.min(Math.max((ms - start) / (end - start), 0), 1) * 1000) / 10;

  const present = new Set<string>(selection.candidates.flatMap((c) => c.source_types));
  const keys = [...LANE_ORDER.filter((k) => present.has(k)), ...[...present].filter((k) => !LANE_ORDER.includes(k)).sort()];
  if (selection.candidates.some((c) => c.on_file)) keys.push("on_file");
  const lanes = keys.map((key) => ({
    key, label: key === "on_file" ? "Vendor master" : key === "contract" ? "Contract" : cap(sourceLabel(key)), note: LANE_NOTE[key],
  }));

  const points = dots.map(({ lane, e164, kind, at }) => ({ lane, e164, kind, pct: pct(Date.parse(at)), at }));

  const ticks: LaneChart["ticks"] = [];
  for (let y = firstYear; Date.UTC(y, 0, 1) <= end; y++) ticks.push({ label: String(y), pct: pct(Date.UTC(y, 0, 1)) });
  const days = selection.rule_settings.min_age_days;
  return {
    start: new Date(start).toISOString().slice(0, 10), end: new Date(end).toISOString().slice(0, 10),
    lanes, points, ticks, cutoffPct: pct(cutoff), minAge: { pct: pct(cutoff - days * DAY_MS), days },
  };
}
