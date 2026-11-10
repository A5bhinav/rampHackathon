// Case states for people to read (spec 0041): the badge label and tone, and the state machine the
// overview draws, copied from docs/state-machine.md (tests/states.test.ts keeps them in step).
// Display only: the database owns the transitions.
import type { CaseState } from "./api-types";
import { words } from "./format.ts";

export const STATE_LABEL: Record<CaseState, string> = {
  open: "Placing hold",
  held: "On hold",
  calling: "Calling vendor",
  retry_wait: "Retry scheduled",
  deciding: "Deciding",
  awaiting_release: "Ready to release",
  needs_review: "Needs review",
  blocked: "Blocked",
  releasing: "Releasing hold",
  released: "Released",
};

export type Tone = "ok" | "warn" | "bad" | "info" | "muted";

export const STATE_TONE: Record<CaseState, Tone> = {
  open: "info", held: "info", calling: "info", retry_wait: "warn", deciding: "info",
  awaiting_release: "ok", needs_review: "warn", blocked: "bad", releasing: "ok", released: "ok",
};

/** The label for a state; one the dashboard doesn't know reads as its own words. */
export function stateLabel(state: string): string {
  return Object.hasOwn(STATE_LABEL, state) ? STATE_LABEL[state as CaseState] : words(state);
}

export function stateTone(state: string): Tone {
  return Object.hasOwn(STATE_TONE, state) ? STATE_TONE[state as CaseState] : "warn";
}

/** The happy path, left to right, and the event that moves each state to the next. */
export const MAIN: CaseState[] = ["open", "held", "calling", "deciding", "awaiting_release", "releasing", "released"];
export const MAIN_EVENTS = ["hold_confirmed", "number_resolved", "call_ended", "decision_made", "release_approved", "hold_release_confirmed"];
/** Drawn under the main row: a retry under calling, review and blocked under deciding and ready. */
export const BRANCH: CaseState[] = ["retry_wait", "needs_review", "blocked"];

/** Moves only a signed-in person can make. */
export const PERSON_EVENTS = ["release_approved", "release_co_approved", "release_rejected", "review_cleared", "review_blocked"];

type Edge = { from: CaseState; event: string; to: CaseState; person: boolean };
const e = (from: CaseState, event: string, to: CaseState): Edge => ({ from, event, to, person: PERSON_EVENTS.includes(event) });

export const EDGES: Edge[] = [
  e("open", "change_detected", "open"), e("open", "hold_confirmed", "held"), e("open", "hold_failed", "needs_review"),
  e("held", "change_detected", "held"), e("held", "command_failed", "needs_review"), e("held", "deadline_reached", "needs_review"),
  e("held", "hold_confirmed", "held"), e("held", "no_provable_number", "needs_review"), e("held", "number_resolved", "calling"),
  e("calling", "call_ended", "deciding"), e("calling", "call_ended", "needs_review"), e("calling", "call_ended", "retry_wait"),
  e("calling", "call_ended", "needs_review"), e("calling", "call_ended", "retry_wait"), e("calling", "call_started", "calling"),
  e("calling", "call_timeout", "needs_review"), e("calling", "change_detected", "held"), e("calling", "command_failed", "needs_review"),
  e("calling", "deadline_reached", "needs_review"),
  e("retry_wait", "change_detected", "held"), e("retry_wait", "command_failed", "needs_review"),
  e("retry_wait", "deadline_reached", "needs_review"), e("retry_wait", "retry_timer_fired", "calling"),
  e("deciding", "change_detected", "held"), e("deciding", "command_failed", "needs_review"), e("deciding", "deadline_reached", "needs_review"),
  e("deciding", "decision_made", "needs_review"), e("deciding", "decision_made", "blocked"), e("deciding", "decision_made", "awaiting_release"),
  e("awaiting_release", "change_detected", "held"), e("awaiting_release", "release_approved", "releasing"),
  e("awaiting_release", "release_co_approved", "awaiting_release"), e("awaiting_release", "release_rejected", "blocked"),
  e("needs_review", "change_detected", "held"), e("needs_review", "review_blocked", "blocked"),
  e("needs_review", "review_cleared", "awaiting_release"),
  e("blocked", "change_detected", "held"),
  e("releasing", "change_detected", "held"), e("releasing", "command_failed", "needs_review"),
  e("releasing", "hold_release_confirmed", "released"),
];
