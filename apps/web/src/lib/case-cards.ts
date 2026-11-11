// The case page's cards, collapsed (spec 0066, PR 2): which one starts open, and each card's
// one-line summary. Display only: every word of state comes from the core's fields; the number card's
// line never prints the number, and accounts show routing and last four only (ADR-8).
import type { CallAttempt, CaseDetail, ChainStatus, DecisionInfo, SelectionView } from "./api-types";
import { brief, words } from "./format.ts";
import { noCallHead, split } from "./lineage.ts";
import type { StationKey, StationView } from "./stations.ts";

export type CardKey = "change" | "hold" | "number" | "call" | "decision";

const CARD_OF: Record<StationKey, CardKey> = {
  detected: "change", held: "hold", number: "number", called: "call", decided: "decision", approved: "decision", released: "decision",
};

/** The card for the station the case is at (or stopped at). Once it's past the decision, the decision. */
export function openCard(steps: readonly Pick<StationView, "key" | "status">[]): CardKey {
  // Where the case waits beats an earlier branch (stationIndex's rule).
  const at = steps.find((s) => s.status === "current") ?? steps.find((s) => s.status === "branched");
  return at ? CARD_OF[at.key] : "decision";
}

export function checkCount(decision: DecisionInfo): { passed: number; total: number; ran: boolean } {
  const items = decision.checklist ?? [];
  return { passed: items.filter((c) => c.status === "passed").length, total: items.length, ran: items.some((c) => c.status !== "not_checked") };
}

/** "Verified · 11 of 11 conditions passed"; when an earlier rule decided, the rules that fired. */
export function decisionLine(decision: DecisionInfo | null, label: string): string | null {
  if (!decision) return null;
  const n = checkCount(decision);
  if (n.total && n.ran) return `${label} · ${n.passed} of ${n.total} conditions passed`;
  const ids = decision.rules_fired.map((id) => id.split("_")[0]);
  return ids.length ? `${label} · ${ids.join(", ")}` : label;
}

type Attempt = Pick<CallAttempt, "call_id" | "attempt" | "result" | "started_at" | "answers" | "dialed_number_is_pick">;

/** Every attempt as a line. Only one of this bank change's calls is expanded: the one the decision
 *  read (its last with answers), else its newest. A call from an earlier change (started before
 *  `versionStart`, as the live call view reads it) is never expanded, and its line says so. */
export function attemptRows<T extends Attempt>(calls: readonly T[], versionStart?: string | null): { call: T; expanded: boolean; line: string }[] {
  const since = Date.parse(versionStart ?? "");
  const current = (c: T) => !Number.isFinite(since) || !c.started_at || Date.parse(c.started_at) >= since;
  const answered = calls.findLastIndex((c) => current(c) && c.answers.length > 0);
  const shown = answered >= 0 ? answered : calls.findLastIndex(current);
  return calls.map((c, i) => {
    const result = c.result ? words(c.result) : "in progress";
    const line = i === shown
      ? [`Attempt ${c.attempt}`, result,  // no "of N": the list can hold earlier versions' calls, each numbered from 1
         c.dialed_number_is_pick === true ? "the Trust Ledger's pick" : c.dialed_number_is_pick === false ? "not the Trust Ledger's pick" : null]
        .filter(Boolean).join(" · ")
      : [`Attempt ${c.attempt}`, result, brief(c.started_at) || null, current(c) ? null : "earlier change"].filter(Boolean).join(" · ");
    return { call: c, expanded: i === shown, line };
  });
}

const acct = (a: { routing_number: string; last4: string }) => `${a.routing_number} ····${a.last4}`;

export function changeLine(change: Pick<CaseDetail["change"], "new_account" | "old_account" | "changed_by">): string {
  const was = change.old_account ? `, was ${acct(change.old_account)}` : "";
  const by = change.changed_by ? `entered by ${change.changed_by.display_name}` : "no known author";
  return `${acct(change.new_account)}${was} · ${by}`;
}

export function holdLine(hold: Pick<NonNullable<CaseDetail["hold"]>, "status" | "confirmed_at" | "released_at"> | null, bills: number): string {
  const n = `${bills} ${bills === 1 ? "bill" : "bills"}`;
  if (!hold) return "Placing the hold";
  if (hold.released_at) return `${n} restored · ${brief(hold.released_at)}`;
  if (hold.status === "failed") return "Not confirmed in Ramp";
  if (hold.confirmed_at) return `${n} on hold · confirmed ${brief(hold.confirmed_at)}`;
  return words(hold.status);
}

export function numberLine(view: Pick<SelectionView, "selection"> | null): string | null {
  if (!view) return null;
  const s = view.selection;
  if (!s) return "No selection recorded";  // the core's note: "No number selection is recorded for this case yet"
  const pick = split(s).pick;
  if (!pick) return noCallHead(s).title;
  const n = pick.source_types.length;
  return `The Trust Ledger's pick · ${n} independent ${n === 1 ? "source" : "sources"} · ${s.rule_version}`;
}

export function trailLine(events: number, chain: Pick<ChainStatus, "ok" | "first_bad_seq">): string {
  return `${events} ${events === 1 ? "event" : "events"} · ${chain.ok ? "chain intact" : `chain broken at #${chain.first_bad_seq}`}`;
}
