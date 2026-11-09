// Pure helpers for the vendor risk list (spec 0026). Every sentence that explains a status comes
// from the API's status_text, written by the ledger; these only pick a label and a colour.
export type RiskStatus = "near_miss" | "unproven" | "missing" | "provable";

const ORDER: RiskStatus[] = ["near_miss", "unproven", "missing", "provable"];
const TONE: Record<RiskStatus, string> = {
  near_miss: "tone-bad", unproven: "tone-warn", missing: "tone-muted", provable: "tone-ok",
};

export function statusLabel(status: RiskStatus): string {
  return status.replaceAll("_", " ");
}

export function statusTone(status: RiskStatus): string {
  return TONE[status] ?? "tone-muted";
}

/** How many vendors have each status, worst first, zeros included. */
export function counts(rows: { status: RiskStatus }[]): [RiskStatus, number][] {
  return ORDER.map((s) => [s, rows.filter((r) => r.status === s).length]);
}
