import type { DecisionInfo } from "@/lib/api-types";
import { when } from "@/lib/format";
import { groupRules } from "@/lib/rules";
import { OutcomeBadge } from "./badges";

/** The decision with every rule that fired, grouped: why it isn't verified, why a person must
 *  look, which conditions weren't met, and which were. Rule ids are shown for auditors. */
export function DecisionDetail({ decision }: { decision: DecisionInfo }) {
  const groups = groupRules(decision.rules_fired, decision.explanation);
  return (
    <>
      <div className="tags"><OutcomeBadge outcome={decision.outcome} /><span className="muted sub">{when(decision.decided_at)}</span><span className="muted sub mono">{decision.engine_version}</span></div>
      {groups ? groups.map((g) => (
        <div key={g.key} className="rule-group">
          <h3 className={`tone-text-${g.tone}`}>{g.title}</h3>
          <ul className="plain">
            {g.rules.map((r) => (
              <li key={r.id}>{r.sentence} <span className="mono muted rule-id" title="Rule id">{r.id}</span></li>
            ))}
          </ul>
        </div>
      )) : (
        <>
          <ul className="plain">{decision.explanation.map((s, i) => <li key={i}>{s}</li>)}</ul>
          <p className="muted">Rules fired: <span className="mono">{decision.rules_fired.join(", ") || "none"}</span></p>
        </>
      )}
    </>
  );
}
