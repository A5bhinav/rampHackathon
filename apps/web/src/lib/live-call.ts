// The phone demo (spec 0065, plan v3 section 4): what the live call page and flow mode draw, built
// only from what the core sends. The state lines ("Choosing a number", "On the call") are fixed
// labels; every reason, effect and summary is the core's own sentence. The last four digits never
// reach this code, only how many were given and the comparison's result word.
import type { CaseDetail, CaseState, SelectionView, TimelineEntry } from "./api-types";
import { SLOT_LABEL, answerText, checkRows, conversation, type CheckRow } from "./call.ts";
import { words } from "./format.ts";
import { phone, split } from "./lineage.ts";
import { stateLabel } from "./states.ts";
import { currentVersion, stations, type StationView } from "./stations.ts";

/** States in which the page keeps asking: a call can still start, end or be decided. */
export const POLL_STATES: ReadonlySet<CaseState> = new Set(["open", "held", "calling", "retry_wait", "deciding"]);

export type Phase = "choosing" | "dialing" | "on_call" | "answers" | "not_connected" | "decided" | "no_call";
export type LiveSlot = { slot: string; label: string; wording: string; answer: string | null; turn: number | null; method: string | null; order: number | null };
export type LiveCallView = {
  caseId: string; version: number; vendor: string; state: CaseState; stateLabel: string; phase: Phase; status: string;
  dialed: string | null; dialedIsPick: boolean | null; attempt: number | null; startedAt: string | null; endedAt: string | null;
  result: string | null; reason: string | null; waiting: string | null; lastStep: string | null;
  disclosure: string | null; note: string | null; slots: LiveSlot[]; checks: CheckRow[];
  outcome: string | null; effect: string | null; steps: StationView[]; poll: boolean;
};

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function liveCallView(detail: CaseDetail, selection: SelectionView | null): LiveCallView {
  const c = detail.case;
  // Only this version's calls: a new bank change restarts the attempts and cuts off a call in
  // progress, so a call that started before the version began is never the current one.
  const since = Date.parse(currentVersion(detail.timeline)[0]?.occurred_at ?? "");
  const calls = (detail.calls ?? []).filter((x) => !Number.isFinite(since) || !x.started_at || Date.parse(x.started_at) >= since)
    .sort((a, b) => (a.started_at ?? "").localeCompare(b.started_at ?? "") || a.attempt - b.attempt);
  const last = calls.at(-1) ?? null;
  // A retry being dialed: the last call ended without answers and the case is calling again.
  const redialing = Boolean(last && last.result && last.result !== "answers_recorded" && c.state === "calling");
  const call = redialing ? null : last;
  const pick = selection?.selection ? split(selection.selection).pick : null;
  const dialed = pick ? phone(pick.e164) : null;
  const script = detail.call_script;

  const phase: Phase = detail.decision ? "decided"
    : call ? (call.result === null ? "on_call" : call.result === "answers_recorded" ? "answers" : "not_connected")
    : c.state === "calling" ? "dialing"
    : c.state === "open" || c.state === "held" ? "choosing"
    : "no_call";
  const status = {
    choosing: "Choosing a number",
    dialing: dialed ? `Dialing ${dialed}` : "Dialing",
    on_call: "On the call",
    answers: "Call ended",
    not_connected: c.state === "retry_wait" ? "Retry scheduled" : capital(words(call?.result ?? "not connected")),
    decided: "Decided",
    no_call: stateLabel(c.state),
  }[phase];

  // The recorded answers, slot by slot in the script's order, numbered by the turn they came from.
  const answers = call?.result === "answers_recorded" || phase === "decided" ? call?.answers ?? [] : [];
  // conversation() lists the role under the name only for an older script with no role question.
  const asked = conversation(script, answers).asked;
  const firstTurn = (a: (typeof asked)[number]) => (a.answers.length ? Math.min(...a.answers.map((x) => x.turn_id)) : null);
  const answeredByTurn = asked.filter((a) => a.answers.length).sort((a, b) => firstTurn(a)! - firstTurn(b)!).map((a) => a.slot);
  const slots: LiveSlot[] = asked.map((a) => {
    const text = a.answers.length ? a.answers.map(answerText).join(" · ") : null;
    const last4 = a.slot === "account_last4" && text && call?.last_four_result ? `${text} · ${words(call.last_four_result)}` : text;
    return {
      slot: a.slot, label: SLOT_LABEL[a.slot] ?? a.slot, wording: a.wording, answer: last4,
      turn: firstTurn(a), method: a.answers[0]?.capture_method ?? null,
      order: a.answers.length ? answeredByTurn.indexOf(a.slot) : null,
    };
  });

  return {
    caseId: c.id, version: c.version, vendor: c.vendor.legal_name, state: c.state, stateLabel: stateLabel(c.state), phase, status,
    dialed, dialedIsPick: call?.dialed_number_is_pick ?? null, attempt: call?.attempt ?? null, startedAt: call?.started_at ?? null, endedAt: call?.ended_at ?? null,
    result: call?.result ?? null, reason: call?.reason ?? null,
    waiting: phase === "on_call" ? "Answers arrive when the call ends." : null,
    lastStep: phase === "no_call" ? detail.timeline.at(-1)?.summary ?? null : null,
    disclosure: script?.disclosure ?? null, note: script?.note ?? null,
    slots, checks: answers.length ? checkRows(call?.checks ?? null) : [],
    outcome: detail.decision?.outcome ?? null, effect: detail.decision?.effect ?? null,
    steps: stations(detail.timeline, c.state, detail.decision?.outcome ?? null),
    poll: POLL_STATES.has(c.state),
  };
}

type Deps = {
  token: () => Promise<string | null>;
  getCase: (caseId: string) => Promise<CaseDetail>;
  getSelection: (caseId: string) => Promise<SelectionView>;
};

/** The live call's polling read, server-side (the route handler wires the real core). Signed out is
 *  401; an unknown case 404; anything else 502. The body is only what the page draws. */
export async function callStateResponse(deps: Deps, caseId: string, withSelection: boolean, datasetId?: string): Promise<{ status: number; body: Partial<LiveCallView> & { error?: string } }> {
  if (!(await deps.token())) return { status: 401, body: { error: "signed out" } };
  try {
    const detail = await deps.getCase(caseId);
    if (datasetId && detail.case.dataset_id !== datasetId) return { status: 404, body: { error: "not found" } };  // another dataset's case
    const selection = withSelection ? await deps.getSelection(caseId).catch((e) => {
      if (String((e as { digest?: unknown }).digest ?? "").startsWith("NEXT_REDIRECT")) throw e;
      return null;
    }) : null;
    return { status: 200, body: liveCallView(detail, selection) };
  } catch (e) {
    // The core client redirects to sign-in when the core refuses the token; for a fetch that is 401.
    if (String((e as { digest?: unknown }).digest ?? "").startsWith("NEXT_REDIRECT")) return { status: 401, body: { error: "signed out" } };
    const status = (e as { status?: number }).status;
    return { status: status === 404 || status === 422 ? 404 : 502, body: { error: "unavailable" } };
  }
}

// Which events each flow-mode column shows: the station's own event, or its branch.
const COLUMN_EVENTS: Record<string, string[]> = {
  detected: ["change_detected"], held: ["hold_confirmed", "hold_failed"], number: ["number_resolved", "no_provable_number"],
  called: ["call_ended", "call_timeout"], decided: ["decision_made"],
  approved: ["release_approved", "release_co_approved", "release_rejected", "review_blocked"], released: ["hold_release_confirmed"],
};

export type FlowColumn = StationView & { summary: string | null; hash: string | null };

/** Flow mode: the seven stations, each with its latest event's time, the core's summary and the short hash. */
export function flowColumns(timeline: readonly TimelineEntry[], state: CaseState, outcome: string | null): FlowColumn[] {
  const version = currentVersion(timeline);
  return stations(timeline, state, outcome).map((s) => {
    const e = s.status === "done" || s.status === "branched" ? [...version].reverse().find((x) => COLUMN_EVENTS[s.key].includes(x.type)) : undefined;
    return { ...s, summary: e?.summary ?? null, hash: e ? e.hash.slice(0, 12) : null, ...(e ? { at: e.occurred_at } : {}) };
  });
}

/** "Watch the call" appears once the current version has a placed call (spec 0066). */
export function canWatchCall(timeline: readonly { type: string }[]): boolean {
  return currentVersion(timeline).some((e) => e.type === "call_started");
}

/** mm:ss for a span of milliseconds. */
export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** An ended call's length; nothing when it took under a second (the text line), never 00:00. */
export function callLength(startedAt: string | null, endedAt: string | null): string | null {
  if (!startedAt || !endedAt) return null;
  const ms = Date.parse(endedAt) - Date.parse(startedAt);
  return Number.isFinite(ms) && ms >= 1000 ? clock(ms) : null;
}

/** The phone frame's lines: the call's status (with its length once it has ended), and whether the
 *  number is the Trust Ledger's pick. Before the call reports, the number shown is the pick itself. */
export function phoneLines(view: Pick<LiveCallView, "phase" | "status" | "dialed" | "dialedIsPick" | "startedAt" | "endedAt" | "result">):
  { status: string; number: string | null; pick: { tone: "ok" | "bad"; text: string } | null } {
  const length = callLength(view.startedAt, view.endedAt);
  const status = view.endedAt && view.result ? ["Call ended", words(view.result), length].filter(Boolean).join(" · ") : view.status;
  const pick = !view.dialed ? null
    : view.dialedIsPick === false ? { tone: "bad" as const, text: "Not the Trust Ledger's pick" }
    : { tone: "ok" as const, text: "The Trust Ledger's pick" };
  // The number shown is the pick's; over a call that reports it dialed something else, show none.
  return { status, number: view.dialedIsPick === false ? null : view.dialed, pick };
}

/** The AP top bar's Present menu: demo datasets only, for the newest case. */
export function presentLinks(kind: string, cases: readonly { id: string; opened_at: string }[], datasetId: string): { label: string; href: string }[] {
  if (kind !== "demo" || cases.length === 0) return [];
  const newest = [...cases].sort((a, b) => b.opened_at.localeCompare(a.opened_at))[0];
  const base = `/d/${datasetId}/cases/${newest.id}`;
  return [{ label: "Live call", href: `${base}/call` }, { label: "Flow mode", href: `${base}/flow` }];
}
