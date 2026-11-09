// The case page's progress strip (spec 0031): five stages, read only from what the core sends
// (the case state, the hold's status, whether a decision exists, the states this version
// visited). It decides nothing; an unknown state shows as waiting on a person, never as released.
import type { CaseState } from "./api-types";

export type StageKey = "hold" | "call" | "decision" | "person" | "release";
export type Stage = {
  key: StageKey;
  label: string;
  status: "done" | "current" | "skipped" | "todo";
  tone: "info" | "ok" | "warn" | "bad";
  note: string;
};
export type ProgressInput = { state: CaseState; visited: readonly CaseState[]; holdStatus: string | null; hasDecision: boolean };

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

export function progress({ state, visited, holdStatus, hasDecision }: ProgressInput): Stage[] {
  let at = AT[state] ?? { index: 3, note: "Needs a person", tone: "warn" as const };
  if (holdStatus === "failed") at = { index: 0, note: "Not confirmed", tone: "bad" };
  // A call is done only when it ended with answers recorded (calling -> deciding); a case that
  // reached calling and went to a person without answers attempted it but did not complete it.
  const answered = visited.includes("deciding");
  const attempted = visited.includes("calling");
  return STAGES.map((s, i): Stage => {
    if (i === at.index) return { ...s, status: "current", tone: at.tone, note: at.note };
    if (i > at.index) return { ...s, status: "todo", tone: "info", note: "" };
    if (s.key === "call" && !answered) return { ...s, status: "skipped", tone: "info", note: attempted ? "Not completed" : "Skipped" };
    if (s.key === "decision" && !hasDecision) return { ...s, status: "skipped", tone: "info", note: "Skipped" };
    return { ...s, status: "done", tone: "info", note: "" };
  });
}
