// The public "How it works" page (spec 0033): the pipeline's seven stations in words, and the
// demo links. Copy only: nothing here decides anything, and no figure here was computed by the
// system, so none is shown.
import type { CaseSummary, Dataset } from "./api-types";

export type StoryKey = "change" | "detect" | "hold" | "number" | "call" | "decide" | "release";
export type StoryStation = { key: StoryKey; label: string; does: string; stops: string; rules: string };

/** The seven stations a bank change passes through, each with what it stops. */
export const STORY_STATIONS: StoryStation[] = [
  { key: "change", label: "A bank change", rules: "",
    does: "A vendor's bank details change in Ramp: a clerk types in new details from an email, or someone else changes them.",
    stops: "Nothing yet. This is where the fraud gets in, so everything after it assumes the change may be fake." },
  { key: "detect", label: "Detect", rules: "",
    does: "Ramp's webhook, a poller and a nightly sweep all compare Ramp's accounts with Dialback's own history, and agree on one case per change.",
    stops: "A missed or forged webhook letting a change slip past: the webhook is only a hint, the comparison is the truth." },
  { key: "hold", label: "Hold", rules: "R1",
    does: "Payments to the vendor are put on hold in Ramp and their dates are saved. Dialback reads the hold back from Ramp before it does anything else.",
    stops: "Money leaving for the new account while anyone is still checking. A hold Ramp won't confirm is an alarm for a person." },
  { key: "number", label: "Pick a provable number", rules: "R2",
    does: "The Trust Ledger picks a phone number seen in at least two independent sources before the request arrived.",
    stops: "Calling the number in the email. A number first seen in the change thread, one digit off a known number, or too new is refused; with none left, nobody is called and a person reviews." },
  { key: "call", label: "Disclosed AI call", rules: "R4 and R8",
    does: "An AI agent calls that number. Its first sentence says it is an AI on a recorded line. It asks who they are, whether they asked for the change, and the new account's last four digits.",
    stops: "A fraudster on the line learning what to say: the agent never says the new details, the digits or what it is checking." },
  { key: "decide", label: "Code decides", rules: "R3",
    does: "A fixed rule table turns the recorded answers into Verified, Not verified or Needs review, and writes a sentence for every rule that fired.",
    stops: "A smooth talker persuading a model. The model only records answers; no tool it has can set an outcome." },
  { key: "release", label: "A second person releases", rules: "R5",
    does: "An approver who did not enter the change reads the evidence, gives a reason, and releases or rejects. Only then does the hold lift.",
    stops: "One person moving money alone. Postgres refuses the person who entered the change, whatever the page shows." },
];

/** The pieces every station leans on. */
export const ACROSS = [
  { title: "Postgres is the referee", rules: "R1 and R5",
    text: "The case state machine, the hold check and the four-eyes release are SQL functions. A bug in the services above can't skip a state or release a payment." },
  { title: "A hash-chained evidence log", rules: "R6",
    text: "Every state change is appended to a log where each row carries the hash of the one before. Editing any row breaks the chain, and a scheduled check names the row." },
  { title: "Fail closed", rules: "R7",
    text: "An error, a timeout, an unclear answer or a hold Ramp won't confirm all end the same way: the hold stays on and a person reviews." },
  { title: "Text is data", rules: "R9",
    text: "Emails, documents and what is said on a call are read as data, never followed as instructions, so a planted \"ignore your rules\" does nothing." },
];

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
