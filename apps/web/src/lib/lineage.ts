// Pure helpers for the number lineage panel (spec 0023). No sentence here explains a decision:
// those come from the API's reason_text and the selection's own reason, written by the ledger.
import type { Candidate, Selection } from "./api-types";

export type Split = { pick: Candidate | null; alternates: Candidate[]; refused: Candidate[] };

/** The pick, then its alternates in rank order, then every number the rule refused. */
export function split(selection: Selection): Split {
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
