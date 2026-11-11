// Pure helpers for the vendor risk list (specs 0026, 0032). Every sentence that explains a status comes
// from the API's status_text, written by the ledger; these only pick a label and a colour.
import { phone } from "./lineage.ts";
export type RiskStatus = "needs_person" | "near_miss" | "unproven" | "missing" | "provable";

const ORDER: RiskStatus[] = ["needs_person", "near_miss", "unproven", "missing", "provable"];
const TONE: Record<RiskStatus, string> = {
  needs_person: "tone-bad", near_miss: "tone-bad", unproven: "tone-warn", missing: "tone-muted", provable: "tone-ok",
};
const LABEL: Partial<Record<RiskStatus, string>> = { needs_person: "needs a person" };  // spec 0079

export function statusLabel(status: RiskStatus): string {
  return LABEL[status] ?? status.replaceAll("_", " ");
}

export function statusTone(status: RiskStatus): string {
  return TONE[status] ?? "tone-muted";
}

/** How many vendors have each status, worst first, zeros included. */
export function counts(rows: { status: RiskStatus }[]): [RiskStatus, number][] {
  return ORDER.map((s) => [s, rows.filter((r) => r.status === s).length]);
}

/** The number on file, character by character, each digit marked where it differs from the
 *  provable number. Display only, and only when the two are a near miss of each other (one digit
 *  off, or two adjacent digits swapped, the ledger's own test): a vendor can be a near miss of a
 *  provable line other than the one shown, and then nothing is marked rather than the wrong digits. */
export function digitMarks(onFile: string, provable: string | null): { ch: string; differs: boolean }[] {
  const a = phone(onFile);
  const chars = [...a].map((ch) => ({ ch, differs: false }));
  const b = provable ? phone(provable) : null;
  if (b === null || a.length !== b.length || a.replace(/\d/g, "0") !== b.replace(/\d/g, "0")) return chars;
  const at = [...a].flatMap((ch, i) => (/\d/.test(ch) ? [i] : []));  // where each digit sits
  const off = at.flatMap((pos, k) => (a[pos] !== b[pos] ? [k] : []));  // which digits differ
  const swapped = off.length === 2 && off[1] === off[0] + 1 && a[at[off[0]]] === b[at[off[1]]] && a[at[off[1]]] === b[at[off[0]]];
  if (off.length === 1 || swapped) for (const k of off) chars[at[k]].differs = true;
  return chars;
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
