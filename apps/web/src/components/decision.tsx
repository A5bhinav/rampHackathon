import type { DecisionInfo } from "@/lib/api-types";
import { when } from "@/lib/format";
import { groupRules } from "@/lib/rules";
import { OutcomeBadge } from "./badges";

const STATUS_TONE: Record<string, string> = { passed: "tone-ok", not_met: "tone-warn", not_checked: "tone-muted" };

/** The decision: the outcome and what it does to the hold, every rule that fired (grouped), then
 *  the full Verified checklist as the core reports it (spec 0036). Rule ids are shown for auditors;
 *  every sentence and status word is the core's. */
export function DecisionDetail({ decision }: { decision: DecisionInfo }) {
  const groups = groupRules(decision.rules_fired, decision.explanation);
  const ran = (decision.checklist ?? []).some((c) => c.status !== "not_checked");
  const listed = new Set(ran ? (decision.checklist ?? []).map((c) => c.id) : []);  // a folded checklist hides nothing recorded
  return (
    <>
      <div className="tags">
        <OutcomeBadge outcome={decision.outcome} />
        <span className="muted sub">{when(decision.decided_at)}</span>
        <span className="muted sub">Engine <span className="mono">{decision.engine_version}</span></span>
      </div>
      {decision.effect ? <p className="effect">{decision.effect}</p> : null}
      {groups ? groups.filter((g) => !(g.key === "met" && listed.size)).map((g) => (
        <div key={g.key} className="rule-group">
          <h3 className={`tone-text-${g.tone}`}>{g.title}</h3>
          <ul className="plain">
            {g.rules.map((r) => (
              <li key={r.id} id={listed.has(r.id) ? undefined : `rule-${r.id}`}>
                {r.sentence} <span className="mono muted rule-id" title="Rule id">{r.id}</span>
              </li>
            ))}
          </ul>
        </div>
      )) : (
        <>
          <ul className="plain">{decision.explanation.map((s, i) => <li key={i}>{s}</li>)}</ul>
          <p className="muted">Rules fired: <span className="mono">{decision.rules_fired.join(", ") || "none"}</span></p>
        </>
      )}
      {decision.checklist?.length ? (
        <Checklist decision={decision} />
      ) : null}
    </>
  );
}

/** Open when the checklist was run; folded when an earlier rule decided and every row is "not checked". */
function Checklist({ decision }: { decision: DecisionInfo }) {
  const items = decision.checklist ?? [];
  const ran = items.some((c) => c.status !== "not_checked");
  const list = (
          <ul className="checklist">
            {items.map((c) => (
              <li key={c.id} id={`rule-${c.id}`} className={`is-${c.status}`}>
                <span className={`badge ${STATUS_TONE[c.status] ?? "tone-muted"}`}>{c.status.replace("_", " ")}</span>
                <span>{c.sentence}</span>
                <span className="mono muted rule-id" title="Rule id">{c.id.split("_")[0]}</span>
              </li>
            ))}
          </ul>
  );
  return (
    <div className="rule-group">
      <h3>The Verified checklist</h3>
      {decision.checklist_note ? <p className="muted sub">{decision.checklist_note}</p> : null}
      {ran ? list : <details className="fold"><summary>Show the {items.length} conditions, not checked</summary>{list}</details>}
    </div>
  );
}
