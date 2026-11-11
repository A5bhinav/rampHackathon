import { notFound } from "next/navigation";
import { CandidatesTable, LaneFigure, Sources } from "@/components/lineage";
import { CardHead, PageHead } from "@/components/page";
import type { SelectionView } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { lanePoints, phone, seenBefore, split } from "@/lib/lineage";

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
          {chart ? <LaneFigure chart={chart} cutoff={s.cutoff} /> : null}
          <CandidatesTable candidates={s.candidates} chosen={chosen} cutoff={s.cutoff} reasonText={view.reason_text} />
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
      <section className="card" aria-labelledby="pick-h">
        <CardHead marker="Chosen" title="No provable number" id="pick-h" meta={<span className="badge tone-bad">No provable number</span>} />
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
