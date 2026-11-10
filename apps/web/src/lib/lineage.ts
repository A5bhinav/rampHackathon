// Pure helpers for the number lineage panel (spec 0023). No sentence here explains a decision:
// those come from the API's reason_text and the selection's own reason, written by the ledger.
import type { Candidate, Selection } from "./api-types";

export type Split = { pick: Candidate | null; alternates: Candidate[]; uncalled: Candidate[]; refused: Candidate[] };
export type Role = "pick" | "alternate" | "uncalled" | "refused";

/** The pick, then its alternates, then the provable numbers nobody calls, then every number the rule
 *  refused. A number is an alternate only if the selection names it as one: under spec 0079 a
 *  withheld selection calls no one, and a picked one may call fewer backups than it ranks, so an
 *  eligible number can be neither the pick nor an alternate ("uncalled"). */
export function split(selection: Selection): Split {
  const ranked = selection.candidates.filter((c) => c.eligible).sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
  const alternateIds = new Set((selection.alternates ?? []).map((a) => a.contact_point_id));
  const pick = selection.outcome === "picked"
    ? selection.candidates.find((c) => c.contact_point_id === selection.contact_point_id) ?? null : null;
  return {
    pick,
    alternates: ranked.filter((c) => alternateIds.has(c.contact_point_id)),
    uncalled: ranked.filter((c) => c.contact_point_id !== pick?.contact_point_id && !alternateIds.has(c.contact_point_id)),
    refused: selection.candidates.filter((c) => !c.eligible),
  };
}

/** What a candidate is to this selection's call list. */
export function roleOf(selection: Selection, candidate: Candidate): Role {
  if (selection.outcome === "picked" && candidate.contact_point_id === selection.contact_point_id) return "pick";
  if ((selection.alternates ?? []).some((a) => a.contact_point_id === candidate.contact_point_id)) return "alternate";
  return candidate.eligible ? "uncalled" : "refused";
}

/** The lineage table's word and tone for a candidate: an alternate is numbered by its place in the
 *  selection's own alternates, never by its rank. */
export function candidateBadge(selection: Selection, candidate: Candidate): { word: string; tone: string } {
  const role = roleOf(selection, candidate);
  if (role === "pick") return { word: "chosen", tone: "tone-ok" };
  if (role === "alternate") {
    const n = (selection.alternates ?? []).findIndex((a) => a.contact_point_id === candidate.contact_point_id) + 1;
    return { word: `alternate ${n}`, tone: "tone-info" };
  }
  return role === "uncalled" ? { word: "provable, not called", tone: "tone-warn" } : { word: "refused", tone: "tone-bad" };
}

/** The heading and badge when a selection calls no one: withheld (spec 0079) or no provable number.
 *  The why is the selection's own reason, written by the ledger. */
export function noCallHead(selection: Selection): { title: string; badge: string } {
  return selection.outcome === "withheld"
    ? { title: "Withheld for a person", badge: "Withheld" }
    : { title: "No provable number", badge: "No provable number" };
}

/** +14155550142 -> (415) 555-0142; anything else as stored. */
export function phone(e164: string): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return m ? `(${m[1]}) ${m[2]}-${m[3]}` : e164;
}

const DAY_MS = 86_400_000;

/** How long before the cutoff a number was first seen, in the largest whole unit. */
export function seenBefore(firstSeen: string, cutoff: string): string {
  const ms = Date.parse(cutoff) - Date.parse(firstSeen);
  const days = Math.floor(ms / DAY_MS);
  if (ms < 0) return "after the request";
  if (days < 1) return "with the request";
  const [n, unit] = days >= 365 ? [Math.floor(days / 365), "year"] : days >= 60 ? [Math.floor(days / 30), "month"] : [days, "day"];
  return `${n} ${unit}${n === 1 ? "" : "s"} before the request`;
}

const SOURCES: Record<string, string> = {
  email: "email", contract: "stored contract", vendor_portal: "vendor portal", listing: "public listing", paper_mail: "paper mail",
};

export function sourceLabel(source: string): string {
  return SOURCES[source] ?? source.replaceAll("_", " ");
}

// The lineage chart (spec 0041): one dot per candidate per source type at its first sighting,
// on a time axis from the January before the earliest sighting to just past the cutoff. Per-
// sighting dots wait for the ledger's lineage endpoint (spec 0041, Q4).
const LANE_ORDER: string[] = ["contract", "vendor_portal", "listing", "email", "paper_mail"];
const LANE_NOTE: Record<string, string> = { email: "counts once", on_file: "never counts" };

export type LanePoint = { lane: string; e164: string; kind: Role; pct: number; at: string };
export type LaneChart = {
  start: string; end: string;
  lanes: { key: string; label: string; note?: string }[];
  points: LanePoint[];
  ticks: { label: string; pct: number }[];
  cutoffPct: number;
  minAge: { pct: number; days: number };
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function lanePoints(selection: Selection | null): LaneChart | null {
  if (!selection || selection.candidates.length === 0) return null;
  const cutoff = Date.parse(selection.cutoff);
  const seen = selection.candidates.map((c) => Date.parse(c.first_seen_at)).filter(Number.isFinite);
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

  const points = selection.candidates.flatMap((c) => {
    const kind = roleOf(selection, c);
    const at = pct(Date.parse(c.first_seen_at));
    return [...c.source_types, ...(c.on_file ? ["on_file"] : [])].map((lane) => ({ lane, e164: c.e164, kind, pct: at, at: c.first_seen_at }));
  });

  const ticks: LaneChart["ticks"] = [];
  for (let y = firstYear; Date.UTC(y, 0, 1) <= end; y++) ticks.push({ label: String(y), pct: pct(Date.UTC(y, 0, 1)) });
  const days = selection.rule_settings.min_age_days;
  return {
    start: new Date(start).toISOString().slice(0, 10), end: new Date(end).toISOString().slice(0, 10),
    lanes, points, ticks, cutoffPct: pct(cutoff), minAge: { pct: pct(cutoff - days * DAY_MS), days },
  };
}
