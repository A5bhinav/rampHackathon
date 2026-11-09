import type { Step } from "@/lib/progress";

const MARK: Record<Step["status"], string> = { done: "✓", current: "", skipped: "–", todo: "" };

/** Where the case is: five stages, the ones passed ticked, a skipped one dashed. */
export function Progress({ steps }: { steps: Step[] }) {
  return (
    <ol className="progress" aria-label="Case progress">
      {steps.map((s) => (
        <li key={s.key} className={`step is-${s.status} tone-${s.tone}`} aria-current={s.status === "current" ? "step" : undefined}>
          <span className="step-dot" aria-hidden="true">{MARK[s.status]}</span>
          <span className="step-text">
            <span className="step-label">{s.label}</span>
            <span className="step-note">{s.status === "skipped" ? "skipped" : s.note ?? (s.status === "done" ? "done" : "")}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
