// The landing page's product pictures (spec 0040). Drawings, not data: every name, bill and
// account comes from the demo seed (scripts/demo/seed.json and the ledger's demo_seed.json),
// every sentence from the product's own wording (the call script, the rule table, the core's
// effect line). Nothing here is computed, and nothing breaks ADR-8: a routing number and the
// last four digits only, and the digits a vendor gives on a call are a count.
import type { StoryKey } from "./story";

export type Tone = "ok" | "warn" | "bad" | "info" | "muted";
export type Turn = { slot: string; ask: string; answer: string };

export type Picture =
  | { kind: "change"; vendor: string; by: string; from: string; to: string; subject: string; sender: string }
  | { kind: "detect"; signals: { name: string; does: string }[]; result: string }
  | { kind: "hold"; vendor: string; bills: { invoice: string; amount: string; due: string }[]; readback: string }
  | { kind: "number"; picked: string; sources: string[]; checks: string[]; refused: { what: string; why: string } }
  | { kind: "call"; disclosure: string; turns: Turn[] }
  | { kind: "decide"; outcome: string; effect: string; rows: { id: string; sentence: string }[]; more: string }
  | { kind: "release"; enteredBy: string; approver: string; reason: string; refusal: string };

const CUSTOMER = "Fictional Co (demo)";

export const SHOWCASE: Record<StoryKey, Picture> = {
  change: {
    kind: "change", vendor: "Acme Fasteners LLC", by: "Pat Clerk",
    from: "Routing 021000021 · ending 2222", to: "Routing 026009593 · ending 0001",
    subject: "Re: Invoice 1638", sender: "ar@acme.example",
  },
  detect: {
    kind: "detect",
    signals: [
      { name: "Ramp webhook", does: "a hint" },
      { name: "Poller", does: "compared with history" },
      { name: "Nightly sweep", does: "compared with history" },
    ],
    result: "One case opened for Acme Fasteners LLC",
  },
  hold: {
    kind: "hold", vendor: "Acme Fasteners LLC",
    bills: [
      { invoice: "ACME-1001", amount: "$12,500.00", due: "due in 3 days" },
      { invoice: "ACME-1002", amount: "$4,800.00", due: "due in 10 days" },
    ],
    readback: "Hold read back from Ramp: confirmed",
  },
  number: {
    kind: "number", picked: "(206) 555-0117",
    sources: ["contract", "vendor master record", "listing", "email"],
    checks: ["Seen in two or more kinds of source", "Seen before the request arrived", "Not one digit off another number"],
    refused: { what: "A number first seen in the request's thread", why: "it may be the fraudster's" },
  },
  call: {
    kind: "call",
    disclosure: `Hi, this is an AI assistant on a recorded line, calling for the accounts payable team at ${CUSTOMER}.`,
    turns: [
      { slot: "recording_consent", ask: "Is it okay to continue on this recorded line?", answer: "Yes" },
      { slot: "company_named", ask: "Could you tell me the name of the company I've reached?", answer: "Acme Fasteners" },
      { slot: "change_requested", ask: `Has your company recently asked ${CUSTOMER} to change how you get paid?`, answer: "Yes" },
      { slot: "account_last4", ask: "For the account you'd like to be paid into, what are the last four digits?", answer: "4 digits given" },
    ],
  },
  decide: {
    kind: "decide", outcome: "Verified",
    effect: "The hold stays on until an approver who didn't enter this change releases it, with a written reason.",
    rows: [
      { id: "V05", sentence: "The person named this vendor." },
      { id: "V07", sentence: "The person confirmed the vendor asked for the change, and is not someone from the request." },
      { id: "V08", sentence: "The digits the person gave match the new account." },
      { id: "V11", sentence: "The disclosure came first and the script was followed." },
    ],
    more: "and the other eight conditions",
  },
  release: {
    kind: "release", enteredBy: "Pat Clerk", approver: "Demo Approver",
    reason: "Acme confirmed the change on the ledger's number; the digits match.",
    refusal: "Pat Clerk entered this change, so Postgres refuses their release.",
  },
};

/** The hero's queue: the three demo changes and where each is set up to end (spec 0011). */
export const HERO_CASES: { vendor: string; state: string; tone: Tone; next: string; amount: string; at: number; skipped: number[] }[] = [
  { vendor: "Acme Fasteners LLC", state: "awaiting release", tone: "ok", next: "ACME-1001", amount: "$12,500.00", at: 3, skipped: [] },
  { vendor: "Brightline Office Supply Inc", state: "needs review", tone: "warn", next: "BOS-2210", amount: "$3,120.00", at: 3, skipped: [1, 2] },
  { vendor: "Cobalt Freight LLC", state: "blocked", tone: "bad", next: "CF-88014", amount: "$27,600.00", at: 3, skipped: [] },
];

export const RAIL = ["Hold", "Call", "Decision", "Sign-off", "Release"];

/** Every string the pictures show (for the ADR-8 test). */
export function allText(): string[] {
  const out: string[] = [];
  const walk = (v: unknown) => {
    if (typeof v === "string") out.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(SHOWCASE);
  walk(HERO_CASES);
  return out;
}
