import { notFound } from "next/navigation";
import { CardHead, PageHead } from "@/components/page";
import type { Candidate, Selection, SelectionView } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { candidateBadge, lanePoints, noCallHead, phone, seenBefore, sourceLabel, split, type LaneChart } from "@/lib/lineage";

// Number lineage (specs 0023, 0041; owner Inesh): the number this case will call, where it came
// from, and every number the Trust Ledger refused, with the ledger's own sentence for each reason.
export default async function LineagePage({ params }: { params: Promise<{ datasetId: string; caseId: string }> }) {
  const { datasetId, caseId } = await params;
  const missing = (e: unknown) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  };
  const [detail, view] = await Promise.all([core.case(caseId).catch(missing), core.selection(caseId).catch(missing)]);
  const back = `/d/${datasetId}/cases/${caseId}`;
  const vendor = detail.case.vendor.legal_name;
  const s = view.selection;
  const pick = s ? split(s).pick : null;
  const chosen = pick?.contact_point_id;  // the same rule as the card above
  const chart = lanePoints(s);

  return (
    <main>
      <PageHead
        back={{ href: back, label: vendor }}
        eyebrow={`Number lineage · ${vendor}`}
        title={pick ? <>Why we call <span className="nowrap">{phone(pick.e164)}</span></> : "Number lineage"}
        lead="The number Dialback will call for this case, where the Trust Ledger saw it, and every number it refused."
      />
      {!s ? (
        <section className="card"><div className="card-body"><p>{view.notes.no_selection}</p></div></section>
      ) : (
        <>
          <Chosen view={view} />
          {chart ? <LaneFigure chart={chart} selection={s} /> : null}
          <section className="card flush" aria-labelledby="all-h">
            <CardHead title={<>Every number considered <span className="count">{s.candidates.length}</span></>} id="all-h" />
            <div className="table-wrap">
              <table className="candidates-table">
                <thead><tr><th>Number</th><th>Result</th><th>Sources</th><th>First seen</th><th className="num">Sightings</th><th>Reasons</th></tr></thead>
                <tbody>
                  {s.candidates.map((c) => {
                    const isPick = c.contact_point_id === chosen;
                    const badge = candidateBadge(s, c);   // an alternate only if the selection calls it (spec 0079)
                    return (
                      <tr key={c.contact_point_id} className={`candidate ${isPick ? "is-ok" : c.eligible ? "" : "is-bad"}`}>
                        <td className="mono candidate-number nowrap">{phone(c.e164)}</td>
                        <td><span className={`badge ${badge.tone}`}>{badge.word}</span></td>
                        <td className="candidate-sources"><Sources candidate={c} />{c.on_file ? <span className="badge tone-muted">on the old vendor master</span> : null}</td>
                        <td className="candidate-seen" title={when(c.first_seen_at)}>{seenBefore(c.first_seen_at, s.cutoff)}</td>
                        <td className="num">{c.observation_count ?? "—"}</td>
                        <td className="candidate-reasons">{c.reasons.length ? <Reasons candidate={c} text={view.reason_text} /> : <span className="muted">—</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
          <dl className="rule-meta" aria-label="The rule that chose">
            <div><dt>Rule</dt><dd className="mono">{s.rule_version}</dd></div>
            <div><dt>Cutoff</dt><dd>{when(s.cutoff)}</dd></div>
            <div><dt>Minimum</dt><dd>{s.rule_settings.min_source_types} source types, {s.rule_settings.min_age_days} days</dd></div>
            <div><dt>Inputs</dt><dd className="mono" title={s.inputs_sha256}>{s.inputs_sha256.slice(0, 12)}</dd></div>
          </dl>
        </>
      )}
    </main>
  );
}

function Chosen({ view }: { view: SelectionView }) {
  const s = view.selection!;
  const { pick, alternates } = split(s);
  if (!pick) {
    const head = noCallHead(s);   // withheld (spec 0079) or no provable number; the why is s.reason
    return (
      <section className="card" aria-labelledby="pick-h">
        <CardHead marker="Chosen" title={head.title} id="pick-h" meta={<span className="badge tone-bad">{head.badge}</span>} />
        <div className="card-body">
          <p>{s.reason}</p>
          <p className="muted">{view.notes.no_provable_number}</p>
        </div>
      </section>
    );
  }
  return (
    <section className="card" aria-labelledby="pick-h">
      <CardHead marker="Chosen" title="The number Dialback will call" id="pick-h" meta={<span className="badge tone-ok">Provable · rank {pick.rank ?? 1}</span>} />
      <div className="card-body pick">
        <p className="phone-figure mono">{phone(pick.e164)}</p>
        <dl className="facts">
          <dt>Seen in</dt><dd className="badges"><Sources candidate={pick} /></dd>
          <dt>First seen</dt><dd>{seenBefore(pick.first_seen_at, s.cutoff)} <span className="muted">({when(pick.first_seen_at)})</span></dd>
          <dt>Sightings</dt><dd>{pick.observation_count ?? "—"}</dd>
          <dt>Alternates</dt>
          <dd><span className="mono">{alternates.length ? alternates.map((a) => phone(a.e164)).join(", ") : "none"}</span>
            <div className="muted sub">{view.notes.alternates}</div></dd>
        </dl>
      </div>
    </section>
  );
}

const KIND_WORD = { pick: "chosen", alternate: "alternate", uncalled: "provable, not called", refused: "refused" } as const;

/** First seen, by source: display only, one dot per number per source at its first sighting. */
function LaneFigure({ chart, selection }: { chart: LaneChart; selection: Selection }) {
  const pick = chart.points.find((p) => p.kind === "pick");
  const n = (k: keyof typeof KIND_WORD) => new Set(chart.points.filter((p) => p.kind === k).map((p) => p.e164)).size;
  const label = `${pick ? `The chosen number ${phone(pick.e164)} was first seen in ${chart.points.filter((p) => p.kind === "pick").length} source lanes before the request. ` : "No number was chosen. "}`
    + `${n("alternate")} alternate, ${n("uncalled")} provable but not called, and ${n("refused")} refused numbers are also drawn, `
    + `against the ${chart.minAge.days}-day minimum age and the request cutoff.`;
  return (
    <figure className="card lineage-chart" aria-labelledby="lanes-title">
      <div className="card-head">
        <span className="section-marker">Lineage</span>
        <h2 id="lanes-title">First seen, by source</h2>
        <ul className="legend card-meta" aria-label="Legend">
          <li><span className="dot-key is-pick" aria-hidden="true" />chosen</li>
          <li><span className="dot-key is-alternate" aria-hidden="true" />alternate</li>
          {n("uncalled") ? <li><span className="dot-key is-uncalled" aria-hidden="true" />provable, not called</li> : null}
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
                  <span key={`${p.e164}-${p.lane}`} className={`lgraph-dot is-${p.kind}`} style={{ left: `${p.pct}%` }}
                        title={`${phone(p.e164)}, ${KIND_WORD[p.kind]}: first seen ${when(p.at)}`} />
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>
      <figcaption className="lgraph-caption muted sub">
        <span><span className="min-key" aria-hidden="true" /> {chart.minAge.days}-day minimum age</span>
        <span><span className="after-key" aria-hidden="true" /> Request received {when(selection.cutoff)}</span>
        <span>Each dot is a number&apos;s first sighting in that source; times in UTC.</span>
      </figcaption>
    </figure>
  );
}

function Sources({ candidate }: { candidate: Candidate }) {
  if (candidate.source_types.length === 0) return <span className="muted">no independent source</span>;
  return <>{candidate.source_types.map((t) => <span key={t} className="badge tone-muted">{sourceLabel(t)}</span>)}</>;
}

function Reasons({ candidate, text }: { candidate: Candidate; text: SelectionView["reason_text"] }) {
  return <ul className="reasons">{candidate.reasons.map((r) => <li key={r}>{text[r]}</li>)}</ul>;
}
