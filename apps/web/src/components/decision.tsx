import type { DecisionInfo } from "@/lib/api-types";
import { when } from "@/lib/format";
import { groupRules } from "@/lib/rules";
import { outcomeOf } from "./badges";

const STATUS: Record<string, { tone: string; word: string }> = {
  passed: { tone: "ok", word: "Passed" }, not_met: { tone: "warn", word: "Not met" }, not_checked: { tone: "muted", word: "Not checked" },
};
const status = (s: string) => (Object.hasOwn(STATUS, s) ? STATUS[s] : { tone: "muted", word: s.replaceAll("_", " ") });

/** The decision: the outcome as a figure, what it does to the hold, every rule that fired
 *  (grouped), then the Verified checklist as the core reports it (specs 0036, 0041). Rule ids are
 *  shown for auditors; every sentence is the core's. */
export function DecisionDetail({ decision }: { decision: DecisionInfo }) {
  const groups = groupRules(decision.rules_fired, decision.explanation);
  const items = decision.checklist ?? [];
  const ran = items.some((c) => c.status !== "not_checked");
  const listed = new Set(ran ? items.map((c) => c.id) : []);  // a folded checklist hides nothing recorded
  const passed = items.filter((c) => c.status === "passed").length;
  const o = outcomeOf(decision.outcome);
  return (
    <>
      <div className="outcome">
        <span className={`outcome-word tone-text-${o.tone}`}>{o.label}</span>
        {items.length && ran ? <span className="muted">{passed} of {items.length} conditions passed</span> : null}
        <span className="muted sub">{when(decision.decided_at)}</span>
      </div>
      {decision.effect ? <p className="effect">{decision.effect}</p> : null}
      {groups ? groups.filter((g) => !(g.key === "met" && listed.size)).map((g) => (
        <div key={g.key} className="rule-group">
          <h3 className={`tone-text-${g.tone}`}>{g.title}</h3>
          <ul className="rule-rows">
            {g.rules.map((r) => (
              <li key={r.id} id={listed.has(r.id) ? undefined : `rule-${r.id}`}>
                <span className="mono rule-id" title={r.id}>{r.id.split("_")[0]}</span>
                <span>{r.sentence}</span>
              </li>
            ))}
          </ul>
        </div>
      )) : (
        <div className="rule-group">
          <ul className="plain">{decision.explanation.map((s, i) => <li key={i}>{s}</li>)}</ul>
          <p className="muted sub">Rules fired: <span className="mono">{decision.rules_fired.join(", ") || "none"}</span></p>
        </div>
      )}
      {items.length ? <Checklist decision={decision} ran={ran} /> : null}
      <p className="decision-foot muted sub">
        Rule table <span className="mono">{decision.engine_version}</span>
      </p>
    </>
  );
}

/** Open when the checklist was run; folded when an earlier rule decided and every row is "not checked". */
function Checklist({ decision, ran }: { decision: DecisionInfo; ran: boolean }) {
  const items = decision.checklist ?? [];
  const list = (
    <ul className="checklist">
      {items.map((c) => {
        const s = status(c.status);
        return (
          <li key={c.id} id={`rule-${c.id}`} className={`is-${c.status}`}>
            <span className="mono rule-id" title={c.id}>{c.id.split("_")[0]}</span>
            <span>{c.sentence}</span>
            <span className={`check-status tone-text-${s.tone}`}>{s.word}</span>
          </li>
        );
      })}
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
