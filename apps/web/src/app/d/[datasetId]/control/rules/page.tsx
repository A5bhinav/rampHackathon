import { CardHead, PageHead } from "@/components/page";
import type { CallScript, SelectionView } from "@/lib/api-types";
import { core } from "@/lib/core";
import { SLOT_LABEL } from "@/lib/call";
import { GUARANTEES, NOT_REPORTED, policyFrom } from "@/lib/settings";

// Rules & policy (spec 0063): the nine guarantees and where each is enforced (fixed product copy),
// then the call's fixed script and the selection rule's settings, both read from the newest case.
// The full decision rule table waits for a generated rules.json (spec 0073).
export default async function RulesPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const cases = await core.allCases(datasetId);
  const newest = [...cases].sort((a, b) => b.opened_at.localeCompare(a.opened_at))[0];
  const [script, view] = newest ? await Promise.all([
    core.case(newest.id).then((d) => d.call_script ?? null).catch((): CallScript | null => null),
    core.selection(newest.id).catch((): SelectionView | null => null),
  ]) : [null, null];
  const p = policyFrom(script, view?.selection ?? null);
  return (
    <main>
      <PageHead eyebrow="Control · settings" title="Rules & policy" lead="What Dialback guarantees, and the fixed script and rule it runs with." />
      <section className="card flush" aria-labelledby="g-h">
        <CardHead marker="R1–R9" title="Guarantees" id="g-h" />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Rule</th><th>Guarantee</th><th>Enforced by</th></tr></thead>
            <tbody>
              {GUARANTEES.map((g) => (
                <tr key={g.id}><td className="mono">{g.id}</td><td>{g.rule}</td><td className="muted">{g.where}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <div className="two-up">
        <section className="card" aria-labelledby="script-h">
          <CardHead marker="Call" title="The call script" id="script-h" meta={<span className="muted sub">from the newest case</span>} />
          <div className="card-body">
            {p.disclosure ? (
              <>
                <div className="disclosure"><span className="disclosure-label">Disclosure, said first</span><p>{p.disclosure}</p></div>
                <ol className="script-list">
                  {p.questions.map((q) => <li key={q.slot}><span className="muted sub">{SLOT_LABEL[q.slot] ?? q.slot}</span><span>{q.wording}</span></li>)}
                </ol>
                {p.note ? <p className="muted sub">{p.note}</p> : null}
              </>
            ) : <p className="muted sub">The call script is {NOT_REPORTED}: no case on this dataset has one.</p>}
          </div>
        </section>
        <section className="card" aria-labelledby="sel-h">
          <CardHead marker="Ledger" title="Number selection" id="sel-h" meta={<span className="muted sub">from the newest case</span>} />
          <div className="card-body">
            {p.rule ? (
              <dl className="facts">
                <dt>Rule</dt><dd className="mono">{p.rule.version}</dd>
                <dt>Source types</dt><dd>at least {p.rule.settings.min_source_types}, independent</dd>
                <dt>Minimum age</dt><dd>{p.rule.settings.min_age_days} days before the request</dd>
                <dt>Near miss</dt><dd>{p.rule.settings.near_miss_distance} digit</dd>
                <dt>Same-subject window</dt><dd>{p.rule.settings.same_subject_window_days} days</dd>
              </dl>
            ) : <p className="muted sub">The selection rule&apos;s settings are {NOT_REPORTED}: no case on this dataset has a selection.</p>}
          </div>
        </section>
      </div>
      <section className="card" aria-labelledby="table-h">
        <CardHead marker="Decision" title="The decision rule table" id="table-h" />
        <div className="card-body"><p className="muted sub">The full rule table is {NOT_REPORTED}. Each case&apos;s decision lists the rules it ran, in the rule table&apos;s own words.</p></div>
      </section>
    </main>
  );
}
