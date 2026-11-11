// Pure helpers for a vendor's history in the Trust Ledger (spec 0104). They pick labels and links;
// every sentence that explains a status or a reason comes from the API, written by the ledger.
import { split, type RuleRun } from "./lineage.ts";

export function ledgerHref(datasetId: string, vendorId: string): string {
  return `/d/${datasetId}/control/trust-ledger/${vendorId}`;
}

export type NumberKind = "pick" | "alternate" | "refused";

/** How the rule sees each number today, as the lane chart marks it, so a number in a document says which row it is. */
export function numberKinds(rule: RuleRun): Map<string, NumberKind> {
  const pick = split(rule).pick?.e164;
  return new Map(rule.candidates.map((c) => [c.e164, c.e164 === pick ? "pick" : c.eligible ? "alternate" : "refused"]));
}

const KINDS: Record<string, string> = { w9: "W-9" };
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function kindLabel(kind: string): string {
  return KINDS[kind] ?? cap(kind.replaceAll("_", " "));
}

const HIDDEN: Record<string, string> = { white: "white text", tiny: "tiny text", off_page: "off the page" };

/** null for text a person could see; otherwise how it was hidden. */
export function hiddenLabel(visibility: string): string | null {
  return visibility === "visible" ? null : `hidden: ${HIDDEN[visibility] ?? visibility.replaceAll("_", " ")}`;
}
