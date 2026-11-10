// The seven stations (spec 0064, plan v3 section 6): every bank change moves through Detected,
// Held, Number chosen, Called, Decided, Approved and Released, and each is already an event in the
// evidence chain. One function feeds the case stepper, the queue's rails and the station counts.
// Display only: it reads the events the core sent and decides nothing; an unknown state never
// shows as approved or released.
import type { CaseState } from "./api-types";
import type { IconName } from "../components/icons";
import { STATE_LABEL } from "./states.ts";

export type StationKey = "detected" | "held" | "number" | "called" | "decided" | "approved" | "released";
export type StationStatus = "done" | "current" | "todo" | "branched";
export type Tone = "info" | "ok" | "warn" | "bad";
export type StationView = { key: StationKey; label: string; status: StationStatus; tone: Tone; marker?: string; at?: string };
export type StationEvent = { type: string; to_state?: CaseState | null; occurred_at?: string; seq?: number };

export const STATIONS: { key: StationKey; label: string; event: string; icon: IconName }[] = [
  { key: "detected", label: "Detected", event: "change_detected", icon: "detect" },
  { key: "held", label: "Held", event: "hold_confirmed", icon: "hold" },
  { key: "number", label: "Number chosen", event: "number_resolved", icon: "ledger" },
  { key: "called", label: "Called", event: "call_ended", icon: "call" },
  { key: "decided", label: "Decided", event: "decision_made", icon: "decision" },
  { key: "approved", label: "Approved", event: "release_approved", icon: "person" },
  { key: "released", label: "Released", event: "hold_release_confirmed", icon: "release" },
];

/** States in which code is still working, so the case page keeps itself fresh. */
export const REFRESH_STATES: ReadonlySet<CaseState> = new Set(["open", "held", "calling", "retry_wait", "deciding", "releasing"]);

/** The current version's events: from the latest change_detected on (all of them if it isn't listed). */
export function currentVersion<T extends StationEvent>(events: readonly T[]): T[] {
  let from = 0;
  events.forEach((e, i) => { if (e.type === "change_detected") from = i; });
  return events.slice(from);
}

type Branch = { tone: Tone; marker: string; at?: string };

export function stations(events: readonly StationEvent[], state: CaseState, outcome: string | null): StationView[] {
  const ev = currentVersion(events);
  const lastOf = (...types: string[]) => [...ev].reverse().find((e) => types.includes(e.type));
  const has = (...types: string[]) => Boolean(lastOf(...types));
  const known = Object.hasOwn(STATE_LABEL, state);
  const decided = has("decision_made");
  const callEnd = ev.find((e, i) => e.type === "call_ended"
    && (e.to_state === "deciding" || (e.to_state == null && ev.slice(i + 1).some((x) => x.type === "decision_made"))));
  const done: Record<StationKey, StationEvent | true | undefined> = {
    detected: lastOf("change_detected") ?? true,  // a case exists, so a change was detected
    held: lastOf("hold_confirmed"),
    number: lastOf("number_resolved"),
    called: callEnd ?? (decided ? true : undefined),
    decided: lastOf("decision_made"),
    approved: lastOf("release_approved") ?? (known && (state === "releasing" || state === "released") ? true : undefined),
    released: lastOf("hold_release_confirmed") ?? (state === "released" ? true : undefined),
  };

  const branch: Partial<Record<StationKey, Branch>> = {};
  const at = (e: StationEvent | undefined) => e?.occurred_at;
  if (!done.held && has("hold_failed")) branch.held = { tone: "bad", marker: "Hold not confirmed", at: at(lastOf("hold_failed")) };
  if (!done.number && has("no_provable_number")) branch.number = { tone: "warn", marker: "No provable number", at: at(lastOf("no_provable_number")) };
  if (!done.called && !decided) {
    const lastCall = lastOf("call_ended", "call_timeout");
    const unreached = lastCall && (lastCall.type === "call_timeout" || lastCall.to_state === "needs_review"
      || (lastCall.to_state == null && state === "needs_review" && lastCall === ev.at(-1)));
    if (unreached) branch.called = { tone: "warn", marker: "No person reached", at: at(lastCall) };
  }
  if (decided && outcome === "not_verified") branch.decided = { tone: "bad", marker: "Not verified", at: at(lastOf("decision_made")) };
  if (decided && outcome === "needs_review") branch.decided = { tone: "warn", marker: "Needs review", at: at(lastOf("decision_made")) };
  if (!done.approved && has("release_rejected", "review_blocked")) branch.approved = { tone: "bad", marker: "Rejected", at: at(lastOf("release_rejected", "review_blocked")) };

  // A deadline or a command that kept failing: amber on the station that was current.
  const stuck = lastOf("deadline_reached", "command_failed");
  if (stuck && Object.keys(branch).length === 0 && state === "needs_review") {
    const k = STATIONS.find((s) => !done[s.key])?.key;
    if (k) branch[k] = { tone: "warn", marker: stuck.type === "deadline_reached" ? "Deadline reached" : "A step could not finish", at: at(stuck) };
  }

  const views: StationView[] = STATIONS.map(({ key, label }) => {
    const b = branch[key];
    if (b) return { key, label, status: "branched", tone: b.tone, marker: b.marker, ...(b.at ? { at: b.at } : {}) };
    const d = done[key];
    if (d) return { key, label, status: "done", tone: "ok", ...(d !== true && d.occurred_at ? { at: d.occurred_at } : {}) };
    return { key, label, status: "todo", tone: "info" };
  });

  const current = (key: StationKey, extra: Partial<StationView> = {}) => {
    const v = views.find((x) => x.key === key)!;
    if (v.status === "todo") Object.assign(v, { status: "current", tone: "info" }, extra);
  };
  const coApproved = has("release_co_approved") ? { tone: "warn" as const, marker: "1 of 2" } : {};
  if (state === "awaiting_release" || (state === "needs_review" && decided)) current("approved", coApproved);
  else if (state === "releasing") current("released");
  else if (known && state !== "released" && Object.keys(branch).length === 0) {
    const first = views.find((v) => v.status === "todo");
    if (first) current(first.key);
  }
  return views;
}

// What a state alone says happened, for a case whose evidence rows aren't in the loaded log.
const IMPLIED: Partial<Record<CaseState, string[]>> = {
  open: [],
  held: ["hold_confirmed"],
  calling: ["hold_confirmed", "number_resolved"],
  retry_wait: ["hold_confirmed", "number_resolved"],
  deciding: ["hold_confirmed", "number_resolved", "call_ended"],
  awaiting_release: ["hold_confirmed", "number_resolved", "call_ended", "decision_made"],
  releasing: ["hold_confirmed", "number_resolved", "call_ended", "decision_made", "release_approved"],
  released: ["hold_confirmed", "number_resolved", "call_ended", "decision_made", "release_approved", "hold_release_confirmed"],
};

type Row = { case_id: string | null; type: string; seq: number; occurred_at?: string };
type CaseLike = { id: string; state: CaseState; outcome: string | null };

/** The same seven stations from the dataset's evidence rows (no to_state): grouped by case, by seq. */
export function stationsFromEvidence(rows: readonly Row[], c: CaseLike): StationView[] {
  const mine = rows.filter((r) => r.case_id === c.id).sort((a, b) => a.seq - b.seq);
  if (mine.length) return stations(mine, c.state, c.outcome);
  const implied = IMPLIED[c.state] ?? (c.outcome ? IMPLIED.awaiting_release! : []);
  return stations([{ type: "change_detected" }, ...implied.map((type) => ({ type, ...(type === "call_ended" ? { to_state: "deciding" as const } : {}) }))],
    c.state, c.outcome);
}

/** Where a case stands: its current or branched station, else the first not done (Released when all are). */
export function stationIndex(views: readonly StationView[]): number {
  const i = views.findIndex((v) => v.status === "current" || v.status === "branched");
  if (i >= 0) return i;
  const todo = views.findIndex((v) => v.status === "todo");
  return todo >= 0 ? todo : views.length - 1;
}

export type StationCount = { key: StationKey; label: string; count: number; markers: { label: string; tone: Tone; count: number }[] };

export function stationCounts(cases: readonly CaseLike[], rows: readonly Row[]): StationCount[] {
  const out: StationCount[] = STATIONS.map((s) => ({ key: s.key, label: s.label, count: 0, markers: [] }));
  for (const c of cases) {
    const views = stationsFromEvidence(rows, c);
    const i = stationIndex(views);
    out[i].count += 1;
    const v = views[i];
    if (v.marker) {
      const m = out[i].markers.find((x) => x.label === v.marker);
      if (m) m.count += 1; else out[i].markers.push({ label: v.marker, tone: v.tone, count: 1 });
    }
  }
  return out;
}
