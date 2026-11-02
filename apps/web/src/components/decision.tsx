import type { DecisionInfo } from "@/lib/api-types";
import { when } from "@/lib/format";
import { groupRules } from "@/lib/rules";
import { OutcomeBadge } from "./badges";

/** The decision with every rule that fired, grouped: why it isn't verified, why a person must
 *  look, what the call didn't show, and what it did. Rule ids are shown for auditors. */
export function DecisionDetail({ decision }: { decision: DecisionInfo }) {
  const groups = groupRules(decision.rules_fired, decision.explanation);
  return (
    <>
      <p><OutcomeBadge outcome={decision.outcome} /> <span className="muted">{when(decision.decided_at)} · {decision.engine_version}</span></p>
      {groups ? groups.map((g) => (
        <div key={g.key} className="rule-group">
          <h3 className={`tone-text-${g.tone}`}>{g.title}</h3>
          <ul className="plain">
            {g.rules.map((r) => (
              <li key={r.id}>{r.sentence} <span className="mono muted rule-id" title="Rule id">{r.id}</span></li>
            ))}
          </ul>
        </div>
      )) : <ul className="plain">{decision.explanation.map((s) => <li key={s}>{s}</li>)}</ul>}
    </>
  );
}
