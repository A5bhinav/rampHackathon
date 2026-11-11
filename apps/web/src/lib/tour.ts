// The guided demo (specs 0039, 0066): the seven demo beats, each opening a real page on the newest
// demo dataset, following the case the presenter's own change opened. Captions say what the page
// shows and what the presenter does there; nothing here decides anything.
import { latestCase } from "./ramp-portal.ts";

export type TourTarget = "change" | "number" | "call" | "decision" | "second" | "tamper" | "lab";
export type TourStep = { n: number; target: TourTarget; title: string; caption: string; action?: { label: string; to: string } };
export type TourIds = { dataset: string; vendor: string | null; kase: string | null };

export const TOUR: TourStep[] = [
  { n: 1, target: "change", title: "The change",
    caption: "A clerk, believing an emailed request, changes Acme's bank details in Ramp. Type a new routing and account number and save. Dialback catches the change and holds Acme's payments first." },
  { n: 2, target: "number", title: "Which number",
    caption: "The number Dialback will call, the independent sources that prove it's Acme's, and every number it refused, such as one first seen in the change request (R2).",
    action: { label: "Open the call", to: "/tour/3" } },
  { n: 3, target: "call", title: "The call",
    caption: "A disclosed AI agent calls that number. It says first that it's an AI on a recorded line, asks fixed questions, and never says the new bank details. Answers fill in as they're recorded.",
    action: { label: "See the decision", to: "/tour/4" } },
  { n: 4, target: "decision", title: "Code decides",
    caption: "A fixed rule table, not the model, turns the answers into the outcome. Show the checks: each condition passed, not met or not checked, in the rule table's own words.",
    action: { label: "Try to release as the clerk", to: "/tour/5" } },
  { n: 5, target: "second", title: "A second person",
    caption: "The clerk who entered the change can't release it (R5), and the page says why. Switch Viewing as to the approver, who releases with a written reason; the hold lifts." },
  { n: 6, target: "tamper", title: "Tamper",
    caption: "The team edits one evidence row by hand, behind the app's back. The chain check names the exact row that changed: an edit can't hide." },
  { n: 7, target: "lab", title: "The attack lab",
    caption: "Scripted scams and legitimate-but-odd calls, replayed through the real pipeline and scored by code. The page shows only results a lab run computed; then follow one change through the whole flow.",
    action: { label: "Show the whole flow", to: "/tour/flow" } },
];

export function tourStep(raw: string): TourStep | undefined {
  return /^[1-7]$/.test(raw) ? TOUR[Number(raw) - 1] : undefined;
}

/** The beat to show: the URL's, else the one this browser tab last opened (kept until End the tour),
 *  so a redirect that drops ?tour= (saving the change, Viewing as, a release) doesn't hide the card. */
export function activeStep(fromUrl: string | null, stored: string | null): TourStep | undefined {
  return tourStep(fromUrl ?? "") ?? tourStep(stored ?? "");
}

/** Acme's vendor id: from the Ramp mock's vendor list, else from one of its cases. */
export function tourVendor(accounts: { vendors: { vendor_id: string; legal_name: string }[] } | null,
  cases: readonly { vendor: { id: string; legal_name: string } }[], legalName: string): string | null {
  return accounts?.vendors.find((v) => v.legal_name === legalName)?.vendor_id
    ?? cases.find((c) => c.vendor.legal_name === legalName)?.vendor.id ?? null;
}

/** Where a beat's one button goes, or null when the beat has none. */
export function tourAction(step: TourStep): string | null {
  return step.action?.to ?? null;
}

/** The case the presenter's change opened: Acme's newest case, by the Ramp mock page's rule. */
export function tourCase<T extends { id: string; version: number; opened_at: string; vendor: { id: string } }>(cases: readonly T[], vendor: string | null): string | null {
  return vendor ? latestCase(cases, vendor)?.id ?? null : null;
}

const change = (ids: TourIds) => (ids.vendor ? `/d/${ids.dataset}/ramp/${ids.vendor}?tour=1` : `/d/${ids.dataset}/ramp?tour=1`);

/** Where a beat goes. Before any change there's no case, so the case beats go back to the change. */
export function tourHref(step: TourStep, ids: TourIds): string {
  const q = `tour=${step.n}`;
  if (step.target === "lab") return `/scorecard?${q}`;
  if (step.target === "tamper") return `/d/${ids.dataset}/control/evidence?${q}`;
  if (step.target === "change" || !ids.kase) return change(ids);
  const kase = `/d/${ids.dataset}/cases/${ids.kase}`;
  if (step.target === "number") return `${kase}/lineage?${q}`;
  if (step.target === "call") return `${kase}/call?${q}`;
  if (step.target === "decision") return `${kase}?${q}#decision-h`;
  return `${kase}?${q}`;
}

/** The last beat's button: flow mode for the same case. */
export function flowHref(ids: TourIds): string {
  return ids.kase ? `/d/${ids.dataset}/cases/${ids.kase}/flow` : change(ids);
}
