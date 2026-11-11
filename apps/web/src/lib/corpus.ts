// The corpus page (spec 0038): the file scripts/corpus/results.py wrote by running the real
// number-picking rule on the frozen corpus. Nothing here runs the rule or judges a result; the
// match verdicts and reasons are the script's and the ledger's.
import data from "../data/corpus-results.json" with { type: "json" };

export type Planted = { e164: string; truth: string; sightings: number; channels: string[]; hidden: "all" | "some" | "none";
  first_seen_at: string | null; eligible: boolean; reasons: string[] };
export type CorpusCase = { id: string; kind: string; source: string | null; title: string; vendor: string;
  request: { subject: string; received_at: string };
  expected: { outcome: string; pick: string | null }; actual: { outcome: string; pick: string | null; reason: string | null };
  matches: boolean; problems: string[]; picked_planted: boolean; planted: Planted[] };
export type Corpus = { generated_by: string; rule_version: string; reason_text: Record<string, string>; cases: CorpusCase[] };

export const CORPUS = data as Corpus;

const KINDS = [
  { kind: "poison", title: "Planted attacks", lead: "A fraudster plants a phone number somewhere the rule reads. The rule must not pick it." },
  { kind: "known_limit", title: "The stated limit", lead: "The one attack the design says this rule can't stop on its own." },
  { kind: "control", title: "Legitimate controls", lead: "Real changes that look odd. The rule must still pick the vendor's real number, or refuse for the right reason." },
];

export function groups(cases: readonly CorpusCase[]) {
  return KINDS.map((k) => ({ ...k, cases: cases.filter((c) => c.kind === k.kind) })).filter((g) => g.cases.length);
}

export function numberVerdict(p: Pick<Planted, "e164" | "eligible" | "reasons"> & { truth?: string }, pick: string | null): { word: string; tone: "ok" | "info" | "bad" } {
  if (p.e164 === pick) return p.truth === "poison" ? { word: "picked: the attacker's", tone: "bad" } : { word: "picked", tone: "ok" };
  return p.eligible ? { word: "eligible, not picked", tone: "info" } : { word: "refused", tone: "bad" };
}

/** Whether the rule picked a number the attacker planted, as the script recorded it. */
export function pickedPlanted(c: CorpusCase): boolean {
  return c.picked_planted === true;
}

/** "Picked a planted number": the stated known limit reads as such, in amber; any other planted pick
 *  is a failure, in red (spec 0066). Counts only, from the frozen results. */
export function plantedStat(cases: readonly CorpusCase[]): { value: number | string; tone: "ok" | "warn" | "bad" } {
  const limits = cases.filter((c) => c.kind === "known_limit" && pickedPlanted(c)).length;
  const failures = cases.filter((c) => c.kind !== "known_limit" && pickedPlanted(c)).length;
  const limit = limits ? `${limits} known ${limits === 1 ? "limit" : "limits"}` : "";
  if (failures) return { value: limit ? `${failures} + ${limit}` : failures, tone: "bad" };
  return limits ? { value: limit, tone: "warn" } : { value: 0, tone: "ok" };
}

export const REASON_SHORT: Record<string, string> = {
  first_seen_in_change_context: "first seen in or after the request", too_few_source_types: "too few sources before the request",
  younger_than_minimum_age: "too new", near_miss_of_older_number: "one digit off, or two swapped",
  hidden_text_only: "only in hidden text", unconfirmed_digits_only: "digits not confirmed", not_vendor_party: "someone else's number",
  fax_line: "a fax line",
};

export const CHANNEL: Record<string, string> = {
  email: "email", contract: "contract", listing: "public listing", vendor_portal: "vendor portal", paper_mail: "paper mail",
};
