import { notFound } from "next/navigation";
import { PageHead } from "@/components/page";
import type { Candidate, SelectionView } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { phone, seenBefore, sourceLabel, split } from "@/lib/lineage";

// Number lineage (spec 0023, owner Inesh): the number this case will call, where it came from,
// and every number the Trust Ledger refused, with the ledger's own sentence for each reason.
export default async function LineagePage({ params }: { params: Promise<{ datasetId: string; caseId: string }> }) {
  const { datasetId, caseId } = await params;
  const missing = (e: unknown) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  };
  const [detail, view] = await Promise.all([core.case(caseId).catch(missing), core.selection(caseId).catch(missing)]);
  const back = `/d/${datasetId}/cases/${caseId}`;
  const s = view.selection;
  const chosen = s ? split(s).pick?.contact_point_id : undefined;  // the same rule as the card above

  return (
    <main>
      <PageHead
        back={{ href: back, label: detail.case.vendor.legal_name }}
        title="Number lineage"
        lead="The number Dialback will call for this case, where the Trust Ledger saw it, and every number it refused."
      />
      {!s ? (
        <section className="card lead-card"><p>{view.notes.no_selection}</p></section>
      ) : (
        <>
          <Chosen view={view} />
          <section className="card flush" aria-labelledby="all-h">
            <h2 id="all-h" className="card-head">Every number considered <span className="count">{s.candidates.length}</span></h2>
            <ul className="candidates">
              {s.candidates.map((c) => {
                const isPick = c.contact_point_id === chosen;
                return (
                  <li key={c.contact_point_id} className={`candidate ${isPick ? "is-ok" : c.eligible ? "is-info" : "is-bad"}`}>
                    <span className="mono candidate-number">{phone(c.e164)}</span>
                    <span>
                      {c.eligible
                        ? <span className={`badge ${isPick ? "tone-ok" : "tone-info"}`}>{isPick ? "chosen" : `alternate ${(c.rank ?? 1) - 1}`}</span>
                        : <span className="badge tone-bad">refused</span>}
                    </span>
                    <span className="candidate-sources"><Sources candidate={c} />{c.on_file ? <span className="muted sub">on the old vendor master</span> : null}</span>
                    <span className="muted sub candidate-seen" title={when(c.first_seen_at)}>First seen {seenBefore(c.first_seen_at, s.cutoff)}</span>
                    {c.reasons.length ? <div className="candidate-reasons"><Reasons candidate={c} text={view.reason_text} /></div> : null}
                  </li>
                );
              })}
            </ul>
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
    return (
      <section className="card lead-card edge-bad" aria-labelledby="pick-h">
        <h2 id="pick-h">No provable number</h2>
        <p>{s.reason}</p>
        <p className="muted">{view.notes.no_provable_number}</p>
      </section>
    );
  }
  return (
    <section className="card lead-card edge-ok" aria-labelledby="pick-h">
      <div className="pick">
        <div>
          <h2 id="pick-h">The number Dialback will call</h2>
          <p className="figure">{phone(pick.e164)}</p>
        </div>
      <dl className="facts">
        <dt>Seen in</dt><dd className="candidate-sources"><Sources candidate={pick} /></dd>
        <dt>First seen</dt><dd>{seenBefore(pick.first_seen_at, s.cutoff)} ({when(pick.first_seen_at)})</dd>
        <dt>Sightings</dt><dd>{pick.observation_count ?? "—"}</dd>
        <dt>Alternates</dt>
        <dd>{alternates.length ? alternates.map((a) => phone(a.e164)).join(", ") : "none"}{" "}
          <div className="muted sub">{view.notes.alternates}</div></dd>
      </dl>
      </div>
    </section>
  );
}

function Sources({ candidate }: { candidate: Candidate }) {
  if (candidate.source_types.length === 0) return <span className="muted">no independent source</span>;
  return <>{candidate.source_types.map((t) => <span key={t} className="badge tone-muted">{sourceLabel(t)}</span>)}</>;
}

function Reasons({ candidate, text }: { candidate: Candidate; text: SelectionView["reason_text"] }) {
  return <ul className="reasons">{candidate.reasons.map((r) => <li key={r}>{text[r]}</li>)}</ul>;
}
