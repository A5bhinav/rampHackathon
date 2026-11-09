// Pure helpers for the vendor risk list (specs 0026, 0032). Every sentence that explains a status comes
// from the API's status_text, written by the ledger; these only pick a label and a colour.
import { phone } from "./lineage.ts";
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

/** The number on file, character by character, each digit marked where it differs from the
 *  provable number (a near miss: one digit off, or two swapped). Display only; compared on the
 *  same formatting, and nothing is marked when the two don't share a shape. */
export function digitMarks(onFile: string, provable: string | null): { ch: string; differs: boolean }[] {
  const a = phone(onFile);
  const b = provable ? phone(provable) : null;
  const comparable = b !== null && a.length === b.length && a.replace(/\d/g, "0") === b.replace(/\d/g, "0");
  return [...a].map((ch, i) => ({ ch, differs: comparable && /\d/.test(ch) && ch !== b![i] }));
}

/** Whole percents that add up to 100 (largest remainder); all zeros stay zeros. */
export function shares(values: readonly number[]): number[] {
  const total = values.reduce((a, b) => a + b, 0);
  if (total === 0) return values.map(() => 0);
  const raw = values.map((v) => (v * 100) / total);
  const out = raw.map(Math.floor);
  const order = raw.map((r, i) => [r - Math.floor(r), i] as const).sort((x, y) => y[0] - x[0] || x[1] - y[1]);
  for (let k = 0; k < 100 - out.reduce((a, b) => a + b, 0); k++) out[order[k][1]] += 1;
  return out;
}
