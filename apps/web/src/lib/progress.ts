// The case page's progress strip. A case moves through five stages; a stage counts as passed only
// if the evidence log shows the current version entering one of its states, so a case that went
// from the hold straight to review shows the call as skipped, not done.
import type { CaseState } from "./api-types";

export type StepStatus = "done" | "current" | "skipped" | "todo";
export type Step = { key: string; label: string; status: StepStatus; tone: "ok" | "warn" | "bad" | "info"; note?: string };

const STAGES: { key: string; label: string; states: string[] }[] = [
  { key: "held", label: "Payments held", states: ["open", "held"] },
  { key: "call", label: "Vendor called", states: ["calling", "retry_wait"] },
  { key: "decide", label: "Code decides", states: ["deciding"] },
  { key: "person", label: "A person reviews", states: ["needs_review", "blocked", "awaiting_release"] },
  { key: "release", label: "Released", states: ["releasing", "released"] },
];

const TONE: Partial<Record<string, Step["tone"]>> = {
  retry_wait: "warn", needs_review: "warn", blocked: "bad", awaiting_release: "ok", releasing: "ok", released: "ok",
};

export function progress(state: CaseState | string, version: number, timeline: { to_state: string; version: number }[]): Step[] {
  const reached = new Set(timeline.filter((e) => e.version === version).map((e) => e.to_state));
  const current = STAGES.findIndex((s) => s.states.includes(state));
  return STAGES.map((s, i) => {
    const status: StepStatus = i === current ? "current"
      : i > current ? "todo"
      : s.states.some((x) => reached.has(x)) ? "done" : "skipped";
    return {
      key: s.key, label: s.label, status,
      tone: status === "current" ? TONE[state] ?? "info" : "info",
      ...(status === "current" ? { note: state.replaceAll("_", " ") } : {}),
    };
  });
}
