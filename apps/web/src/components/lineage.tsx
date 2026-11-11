import { CardHead } from "@/components/page";
import type { Candidate, SelectionView } from "@/lib/api-types";
import { when } from "@/lib/format";
import { phone, seenBefore, sourceLabel, type LaneChart } from "@/lib/lineage";

// The lineage chart and the every-number table (specs 0023, 0041), shared by a case's number lineage
// and a vendor's history in the Trust Ledger (spec 0104). `what` names the cutoff: "the request" for
// a case, "the cutoff" for the rule as of now. Every reason sentence is the ledger's reason_text.

type ReasonText = SelectionView["reason_text"];

export function CandidatesTable({ candidates, chosen, cutoff, reasonText, what = "the request" }: {
  candidates: Candidate[]; chosen: string | undefined; cutoff: string; reasonText: ReasonText; what?: string;
}) {
  return (
    <section className="card flush" aria-labelledby="all-h">
      <CardHead title={<>Every number considered <span className="count">{candidates.length}</span></>} id="all-h" />
      <div className="table-wrap">
        <table className="candidates-table">
          <thead><tr><th>Number</th><th>Result</th><th>Sources</th><th>First seen</th><th className="num">Sightings</th><th>Reasons</th></tr></thead>
          <tbody>
            {candidates.map((c) => {
              const isPick = c.contact_point_id === chosen;
              return (
                <tr key={c.contact_point_id} className={`candidate ${isPick ? "is-ok" : c.eligible ? "" : "is-bad"}`}>
                  <td className="mono candidate-number nowrap">{phone(c.e164)}</td>
                  <td>
                    {c.eligible
                      ? <span className={`badge ${isPick ? "tone-ok" : "tone-info"}`}>{isPick ? "chosen" : `alternate ${(c.rank ?? 1) - 1}`}</span>
                      : <span className="badge tone-bad">refused</span>}
                  </td>
                  <td className="candidate-sources"><Sources candidate={c} />{c.on_file ? <span className="badge tone-muted">on the old vendor master</span> : null}</td>
                  <td className="candidate-seen" title={when(c.first_seen_at)}>{seenBefore(c.first_seen_at, cutoff, what)}</td>
                  <td className="num">{c.observation_count ?? "-"}</td>
                  <td className="candidate-reasons">{c.reasons.length ? <Reasons candidate={c} text={reasonText} /> : <span className="muted">-</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const KIND_WORD = { pick: "chosen", alternate: "alternate", refused: "refused" } as const;

/** First seen, by source: display only, one dot per number per source at its first sighting. */
export function LaneFigure({ chart, cutoff, what = "the request", cutoffName = "the request cutoff", cutoffLabel = "Request received" }: {
  chart: LaneChart; cutoff: string; what?: string; cutoffName?: string; cutoffLabel?: string;
}) {
  const pick = chart.points.find((p) => p.kind === "pick");
  const n = (k: keyof typeof KIND_WORD) => new Set(chart.points.filter((p) => p.kind === k).map((p) => p.e164)).size;
  const label = `${pick ? `The chosen number ${phone(pick.e164)} was first seen in ${chart.points.filter((p) => p.kind === "pick").length} source lanes before ${what}. ` : "No number was chosen. "}`
    + `${n("alternate")} alternate and ${n("refused")} refused numbers are also drawn, against the ${chart.minAge.days}-day minimum age and ${cutoffName}.`;
  return (
    <figure className="card lineage-chart" aria-labelledby="lanes-title">
      <div className="card-head">
        <span className="section-marker">Lineage</span>
        <h2 id="lanes-title">First seen, by source</h2>
        <ul className="legend card-meta" aria-label="Legend">
          <li><span className="dot-key is-pick" aria-hidden="true" />chosen</li>
          <li><span className="dot-key is-alternate" aria-hidden="true" />alternate</li>
          <li><span className="dot-key is-refused" aria-hidden="true" />refused</li>
        </ul>
      </div>
      <div className="card-body">
        <div className="lgraph" role="img" aria-label={label}>
          <div className="lgraph-row lgraph-axis">
            <span />
            <span className="lgraph-track">
              {chart.ticks.map((t) => <span key={t.label} className="lgraph-tick" style={{ left: `${t.pct}%` }}>{t.label}</span>)}
            </span>
          </div>
          {chart.lanes.map((lane) => (
            <div key={lane.key} className={`lgraph-row${lane.key === "on_file" ? " is-muted" : ""}`}>
              <span className="lgraph-name">{lane.label}{lane.note ? <span className="muted"> · {lane.note}</span> : null}</span>
              <span className="lgraph-track">
                {chart.ticks.map((t) => <span key={t.label} className="lgraph-grid" style={{ left: `${t.pct}%` }} />)}
                <span className="lgraph-min" style={{ left: `${chart.minAge.pct}%` }} />
                <span className="lgraph-after" style={{ left: `${chart.cutoffPct}%` }} />
                {chart.points.filter((p) => p.lane === lane.key).map((p) => (
                  <span key={`${p.e164}-${p.lane}`} className={`lgraph-dot is-${p.kind}`}
                        style={{ left: `${p.pct}%`, top: `calc(50% + ${Math.max(-1, Math.min(1, p.lift)) * 13}px)` }}
                        title={`${phone(p.e164)}, ${KIND_WORD[p.kind]}: first seen ${when(p.at)}`} />
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>
      <figcaption className="lgraph-caption muted sub">
        <span><span className="min-key" aria-hidden="true" /> {chart.minAge.days}-day minimum age</span>
        <span><span className="after-key" aria-hidden="true" /> {cutoffLabel} {when(cutoff)}</span>
        <span>Each dot is a number&apos;s first sighting in that source; times in UTC.</span>
      </figcaption>
    </figure>
  );
}

export function Sources({ candidate }: { candidate: Candidate }) {
  if (candidate.source_types.length === 0) return <span className="muted">no independent source</span>;
  return <>{candidate.source_types.map((t) => <span key={t} className="badge tone-muted">{sourceLabel(t)}</span>)}</>;
}

function Reasons({ candidate, text }: { candidate: Candidate; text: ReasonText }) {
  return <ul className="reasons">{candidate.reasons.map((r) => <li key={r}>{text[r]}</li>)}</ul>;
}
