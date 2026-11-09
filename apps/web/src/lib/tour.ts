// The guided demo (spec 0039): spec 0011's beats as eight steps, each opening the real page on the
// demo data. Captions say what the page shows and what the team does; nothing here decides anything.
export type TourTarget = "risk" | "queue" | "lineage" | "call" | "decision" | "release" | "evidence" | "results";
export type TourStep = { n: number; target: TourTarget; title: string; caption: string };

export const TOUR: TourStep[] = [
  { n: 1, target: "risk", title: "Vendor risk",
    caption: "Before any change: is each vendor's phone number on file one the Trust Ledger can prove? A near miss is a number one digit off the provable one." },
  { n: 2, target: "queue", title: "A bank change, held",
    caption: "The team makes a bank change in the Ramp mock (dialback-core demo change). Dialback opens a case and holds the vendor's payments before anything else." },
  { n: 3, target: "lineage", title: "Why this number",
    caption: "The number Dialback will call for Acme, the independent sources that prove it, and every number it refused, such as one first seen in the change request." },
  { n: 4, target: "call", title: "The call",
    caption: "Scroll to the call: the AI disclosure first, each question, and what was recorded. No transcript is kept, and the last four digits only as a count." },
  { n: 5, target: "decision", title: "Code decides",
    caption: "A fixed rule table, not the model, turned the answers into the outcome. Each condition shows passed, not met or not checked, in the rule table's words." },
  { n: 6, target: "release", title: "A second person releases",
    caption: "The clerk who entered the change can't release it (R5). An approver gives a written reason and releases; the hold lifts and the saved payment dates come back." },
  { n: 7, target: "evidence", title: "Tamper with the evidence",
    caption: "The team edits one evidence row by hand (dialback-core demo tamper). The chain check names the row: an edit can't hide." },
  { n: 8, target: "results", title: "Results",
    caption: "The number-picking rule against 20 frozen cases, including the one it is known to miss. The full attack lab isn't built, so there is no scorecard." },
];

export function tourStep(raw: string): TourStep | undefined {
  return /^[1-8]$/.test(raw) ? TOUR[Number(raw) - 1] : undefined;
}

/** Where a step goes, given the newest demo dataset and its Acme case (null if there is none). */
export function tourHref(step: TourStep, ids: { dataset: string; acme: string | null }): string {
  const q = `tour=${step.n}`;
  const ds = `/d/${ids.dataset}`;
  if (step.target === "results") return `/corpus?${q}`;
  if (step.target === "risk") return `${ds}/risk?${q}`;
  if (step.target === "queue") return `${ds}/cases?${q}`;
  if (step.target === "evidence") return `${ds}/evidence?${q}`;
  if (!ids.acme) return `${ds}/cases?${q}&missing=acme`;
  const kase = `${ds}/cases/${ids.acme}`;
  if (step.target === "lineage") return `${kase}/lineage?${q}`;
  if (step.target === "decision") return `${kase}?${q}#decision-h`;
  return `${kase}?${q}`;
}
