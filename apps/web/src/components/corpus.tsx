import { Stats } from "./page";
import { CHANNEL, CORPUS, REASON_SHORT, groups, numberVerdict, pickedPlanted, plantedStat, type CorpusCase } from "@/lib/corpus";
import { phone } from "@/lib/lineage";

// The number-picking rule against the frozen corpus (specs 0038, 0063), shared by the public /corpus
// page and Control's Assurance page. Static: every verdict was written by scripts/corpus/results.py
// running the real rule; this only lays it out.
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "—");

export function CorpusStats() {
  const all = CORPUS.cases;
  const matched = all.filter((c) => c.matches).length;
  return (
    <Stats label="Results" items={[
      { label: "Cases", value: all.length },
      { label: "Matches the frozen truth", value: `${matched} of ${all.length}`, tone: matched === all.length ? "ok" : undefined },
      { label: "Picked a planted number", ...plantedStat(all) },
    ]} />
  );
}

export function CorpusGroups() {
  return (
    <>
        {groups(CORPUS.cases).map((g) => (
          <section key={g.kind} className="corpus-group" aria-labelledby={`g-${g.kind}`}>
            <div className="group-head">
              <h2 id={`g-${g.kind}`}>{g.title} <span className="count">{g.cases.length}</span></h2>
              <p className="muted">{g.lead}</p>
            </div>
            <div className="card flush corpus-list">
              <div className="corpus-row corpus-cols" aria-hidden="true">
                <span>ID</span><span>Pattern</span><span>Vendor</span><span>Expected</span><span>The rule</span><span>Result</span>
              </div>
              {g.cases.map((c) => <Case key={c.id} c={c} />)}
            </div>
          </section>
        ))}
        <p className="muted sub corpus-foot">Open a row for the numbers the rule saw. &ldquo;First seen&rdquo; is the rule&apos;s own reading; hover a reason for the ledger&apos;s own sentence.</p>
    </>
  );
}

function Case({ c }: { c: CorpusCase }) {
  const planted = pickedPlanted(c);
  const limit = c.kind === "known_limit";
  return (
    <details className={`corpus-case${limit ? " is-limit" : planted ? " is-bad" : ""}`} id={c.id}>
      <summary className="corpus-row" id={`${c.id}-h`}>
        <span className="mono muted">{c.id}</span>
        <span className="corpus-title">{c.title}</span>
        <span><span className="sr-only">Vendor: </span>{c.vendor}</span>
        <span className="mono"><span className="sr-only">Expected: </span>{c.expected.pick ? phone(c.expected.pick) : "no number"}</span>
        <span className="mono"><span className="sr-only">The rule: </span>{c.actual.pick ? phone(c.actual.pick) : "no number"}</span>
        <span className="tags">
          {limit ? <span className="badge tone-warn">Known limit</span> : null}
          {c.source === "proposed" ? <span className="badge tone-muted" title="Spec 0020, Q42">proposed</span> : null}
          <span className={`badge ${c.matches ? "tone-ok" : "tone-bad"}`}>{c.matches ? "matches the truth" : "differs"}</span>
        </span>
      </summary>
      <div className="corpus-detail">
      <p className="muted sub">
        Change request &ldquo;{c.request.subject}&rdquo; · expected:{" "}
        {c.expected.pick ? <span className="mono">{phone(c.expected.pick)}</span> : "no number (a person reviews)"} · the rule:{" "}
        {c.actual.pick ? <span className="mono">{phone(c.actual.pick)}</span> : "no provable number"}
      </p>
      {planted && limit ? (
        <p className="result error">
          The rule picks the number the attacker planted, as the design says it will. The attacker has controlled the vendor&apos;s
          mailbox and a public listing for months, which this rule counts as two independent sources; the vendor&apos;s real numbers
          were only ever seen by email, which counts as one. With no stored contract to contradict them, the attacker answers the
          call too, so the call isn&apos;t a backstop here. The second approver, reading the evidence, is.
        </p>
      ) : planted ? <p className="result error">The rule picked a number the attacker planted.</p> : null}
      {c.problems.length ? <p className="result error">{c.problems.join("; ")}</p> : null}
      <div className="table-wrap">
        <table className="corpus-table">
          <thead><tr><th>Number</th><th>Whose</th><th>The rule</th><th>Why</th><th className="num">Seen</th><th>Where</th><th>First seen</th></tr></thead>
          <tbody>
            {c.planted.map((p) => {
              const v = numberVerdict(p, c.actual.pick);
              return (
                <tr key={p.e164} className={p.truth === "poison" ? "is-poison" : ""}>
                  <td className="mono nowrap">{phone(p.e164)}</td>
                  <td>{p.truth === "poison" ? <span className="badge tone-bad">planted by the attacker</span> : <span className="badge tone-muted">the vendor&apos;s</span>}</td>
                  <td><span className={`badge tone-${v.tone}`}>{v.word}</span></td>
                  <td>
                    {p.reasons.length ? (
                      <ul className="reason-chips">
                        {p.reasons.map((r) => <li key={r} title={CORPUS.reason_text[r] ?? r}>{REASON_SHORT[r] ?? r.replaceAll("_", " ")}</li>)}
                      </ul>
                    ) : <span className="muted">—</span>}
                  </td>
                  <td className="num">{p.sightings}</td>
                  <td>{p.channels.map((ch) => CHANNEL[ch] ?? ch).join(", ")}{p.hidden !== "none" ? <div className="cell-note muted sub">{p.hidden === "all" ? "only in hidden text" : "partly in hidden text"}</div> : null}</td>
                  <td className="nowrap">{day(p.first_seen_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted sub">The change request arrived {day(c.request.received_at)}.</p>
      </div>
    </details>
  );
}
