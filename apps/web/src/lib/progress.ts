// The case page's progress strip (spec 0031): five stages, read only from what the core sends
// (the case state, the hold's status, whether a decision exists, the states this version
// visited). It decides nothing and gates nothing; an unknown state shows as waiting on a
// person, never as released, and a stage is ticked only when the facts say it finished.
import type { CaseState, HoldStatus } from "./api-types";

export type StageKey = "hold" | "call" | "decision" | "person" | "release";
export type Stage = {
  key: StageKey;
  label: string;
  status: "done" | "current" | "skipped" | "failed" | "todo";
  tone: "info" | "ok" | "warn" | "bad";
  note: string;
};
export type ProgressInput = { state: CaseState; visited: readonly CaseState[]; holdStatus: HoldStatus | null; hasDecision: boolean };

const STAGES: { key: StageKey; label: string }[] = [
  { key: "hold", label: "Hold" },
  { key: "call", label: "Call" },
  { key: "decision", label: "Decision" },
  { key: "person", label: "Sign-off" },
  { key: "release", label: "Release" },
];

// Where each state sits, with the note and tone the current stage shows.
const AT: Record<CaseState, { index: number; note: string; tone: Stage["tone"] }> = {
  open: { index: 0, note: "Placing", tone: "info" },
  held: { index: 1, note: "Picking a number", tone: "info" },
  calling: { index: 1, note: "Calling", tone: "info" },
  retry_wait: { index: 1, note: "Retrying later", tone: "info" },
  deciding: { index: 2, note: "Deciding", tone: "info" },
  needs_review: { index: 3, note: "Needs review", tone: "warn" },
  awaiting_release: { index: 3, note: "Awaiting approver", tone: "ok" },
  blocked: { index: 3, note: "Blocked", tone: "bad" },
  releasing: { index: 4, note: "Releasing", tone: "ok" },
  released: { index: 5, note: "", tone: "ok" },
};
const UNKNOWN = { index: 3, note: "Needs a person", tone: "warn" as const };

/** Where a state sits on the rail (0 hold … 4 release, 5 past release); unknown is sign-off. */
export function stationOf(state: CaseState): { index: number; note: string; tone: Stage["tone"] } {
  return Object.hasOwn(AT, state) ? AT[state] : UNKNOWN;  // never a prototype key
}
const HOLD_IN_PLACE: readonly (HoldStatus | null)[] = ["confirmed", "releasing", "released"];

/** The states this case visited in its current version: a new bank change starts the strip again. */
export function visitedThisVersion(timeline: readonly { version: number; to_state: CaseState }[], version: number): CaseState[] {
  return timeline.filter((e) => e.version === version).map((e) => e.to_state);
}

export function progress({ state, visited, holdStatus, hasDecision }: ProgressInput): Stage[] {
  const at = stationOf(state);
  // A call is done only when it ended with answers recorded (calling -> deciding); a decision
  // only when one exists. Reaching the stage without finishing it is "not completed".
  const reached = (s: CaseState) => visited.includes(s);
  return STAGES.map((s, i): Stage => {
    if (s.key === "hold" && holdStatus === "failed" && i !== at.index) {
      return { ...s, status: "failed", tone: "bad", note: "Not confirmed" };
    }
    if (i === at.index) {
      return holdStatus === "failed" ? { ...s, status: "current", tone: "bad", note: "Not confirmed" } : { ...s, status: "current", tone: at.tone, note: at.note };
    }
    if (i > at.index) return { ...s, status: "todo", tone: "info", note: "" };
    if (s.key === "hold" && !HOLD_IN_PLACE.includes(holdStatus)) return { ...s, status: "failed", tone: "bad", note: "Not confirmed" };
    if (s.key === "call" && !reached("deciding")) return { ...s, status: "skipped", tone: "info", note: reached("calling") ? "Not completed" : "Skipped" };
    if (s.key === "decision" && !hasDecision) return { ...s, status: "skipped", tone: "info", note: reached("deciding") ? "Not completed" : "Skipped" };
    return { ...s, status: "done", tone: "info", note: "" };
  });
}
