// The evidence chain page (spec 0037): words for what Postgres's checks found. Display only:
// content_ok, link_ok and the first bad row all come from the core.
import type { ChainStatus, EvidenceRow } from "./api-types";

export type RowStatus = { word: string; tone: "ok" | "bad"; first: boolean };

export function rowStatus(row: Pick<EvidenceRow, "seq" | "content_ok" | "link_ok">, firstBad: number | null): RowStatus {
  const problems = [...(row.content_ok ? [] : ["body edited"]), ...(row.link_ok ? [] : ["link broken"])];
  return { word: problems.length ? problems.join(", ") : "intact", tone: problems.length ? "bad" : "ok", first: row.seq === firstBad };
}

export function chainHeadline(chain: Pick<ChainStatus, "ok" | "checked" | "first_bad_seq">): string {
  if (!chain.ok) return `Broken at row #${chain.first_bad_seq}`;
  if (chain.checked === 0) return "Empty: no rows yet";
  return `Intact: Postgres recomputed all ${chain.checked} rows just now`;
}
