// The demo's stories and links (specs 0033, 0063): the seeded vendors, where sign-in may send someone
// back to, and finding the newest demo dataset and its case. Nothing here decides anything.
import type { CaseSummary, Dataset } from "./api-types";

export type DemoStory = { ref: string; legal_name: string; title: string; story: string };

/** The demo seed's three bank changes (spec 0011, decision 3): what each is set up to show. */
export const DEMO_STORIES: DemoStory[] = [
  { ref: "acme", legal_name: "Acme Fasteners LLC", title: "Confirmed",
    story: "Acme's contact, on the number the Trust Ledger can prove, confirms the change and gives matching digits. Code says Verified; the clerk who entered the change can't release it, and an approver does." },
  { ref: "brightline", legal_name: "Brightline Office Supply Inc", title: "Refused before dialing",
    story: "The voice service's pre-dial check refuses Brightline's call. Nobody is called, the hold stays on, and the case waits for a person." },
  { ref: "cobalt", legal_name: "Cobalt Freight LLC", title: "Denied",
    story: "Cobalt's contact says the company never asked to change banks. Code says Not verified, and the hold stays on." },
];

/** Where to go after signing in: a known demo link, or the dashboard. Nothing else is followed,
 *  so the parameter can't send anyone off the site. */
export function safeNext(raw: unknown): string {
  if (typeof raw !== "string") return "/app";
  if (/^\/tour\/[1-8]$/.test(raw)) return raw;  // the guided demo (spec 0039)
  if (raw === "/app/control") return raw;  // the front door's Control button (spec 0063)
  // A dataset page, for "Viewing as" (spec 0041): a uuid, then plain lower-case path segments only.
  if (/^\/d\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\/[a-z0-9-]+)*$/.test(raw)) return raw;
  const m = /^\/demo\/([a-z]+)$/.exec(raw);
  return m && demoStory(m[1]) ? raw : "/app";
}

export function demoStory(ref: string): DemoStory | undefined {
  return DEMO_STORIES.find((s) => s.ref === ref);
}

/** The newest demo dataset: the core lists datasets newest first. */
export function newestDemo(datasets: readonly Dataset[]): Dataset | undefined {
  return datasets.find((d) => d.kind === "demo");
}

/** The vendor's newest case, matched on its exact legal name. */
export function caseFor<T extends Pick<CaseSummary, "vendor" | "opened_at">>(cases: readonly T[], legalName: string): T | undefined {
  return cases.filter((c) => c.vendor.legal_name === legalName)
    .sort((a, b) => b.opened_at.localeCompare(a.opened_at))[0];
}
