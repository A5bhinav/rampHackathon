import Link from "next/link";
import { notFound } from "next/navigation";
import type { Candidate, SelectionView } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { phone, seenBefore, sourceLabel, split } from "@/lib/lineage";

// Number lineage (spec 0017, owner Inesh): the number this case will call, where it came from,
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

  return (
    <main>
      <p><Link href={back}>← {detail.case.vendor.legal_name}</Link></p>
      <h1>Number lineage</h1>
      {!s ? (
        <section className="card">
          <p>No number selection is recorded for this case yet.</p>
          <p className="muted">Until the Trust Ledger records one, no call is made and the hold stays on.</p>
        </section>
      ) : (
        <>
          <Chosen view={view} />
          <section className="card" aria-labelledby="all-h">
            <h2 id="all-h">Every number considered</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Number</th><th>Result</th><th>Seen in</th><th>First seen</th><th>Why</th></tr></thead>
                <tbody>
                  {s.candidates.map((c) => (
                    <tr key={c.contact_point_id}>
                      <td className="mono">{phone(c.e164)}{c.on_file ? <div className="muted">on the old vendor master</div> : null}</td>
                      <td>{c.eligible
                        ? <span className={`badge ${c.rank === 1 && s.outcome === "picked" ? "tone-ok" : "tone-info"}`}>
                            {c.rank === 1 ? "chosen" : `alternate ${(c.rank ?? 1) - 1}`}</span>
                        : <span className="badge tone-bad">refused</span>}</td>
                      <td><Sources candidate={c} /></td>
                      <td title={when(c.first_seen_at)}>{seenBefore(c.first_seen_at, s.cutoff)}</td>
                      <td><Reasons candidate={c} text={view.reason_text} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <p className="muted">
            Rule <span className="mono">{s.rule_version}</span> · cutoff {when(s.cutoff)} · minimum{" "}
            {s.rule_settings.min_source_types} source types and {s.rule_settings.min_age_days} days · inputs{" "}
            <span className="mono" title={s.inputs_sha256}>{s.inputs_sha256.slice(0, 12)}</span>
          </p>
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
        <h2 id="pick-h">No provable number</h2>
        <p>{s.reason}</p>
        <p className="muted">No call is made; the case waits for a person.</p>
      </section>
    );
  }
  return (
    <section className="card" aria-labelledby="pick-h">
      <h2 id="pick-h">The number Dialback will call</h2>
      <p className="mono" style={{ fontSize: "1.4rem", margin: "4px 0" }}>{phone(pick.e164)}</p>
      <dl className="facts">
        <dt>Seen in</dt><dd><Sources candidate={pick} /></dd>
        <dt>First seen</dt><dd>{seenBefore(pick.first_seen_at, s.cutoff)} ({when(pick.first_seen_at)})</dd>
        <dt>Sightings</dt><dd>{pick.observation_count ?? "—"}</dd>
        <dt>Alternates</dt>
        <dd>{alternates.length ? alternates.map((a) => phone(a.e164)).join(", ") : "none"}{" "}
          <span className="muted">(used only if this line is dead)</span></dd>
      </dl>
    </section>
  );
}

function Sources({ candidate }: { candidate: Candidate }) {
  if (candidate.source_types.length === 0) return <span className="muted">no independent source</span>;
  return <>{candidate.source_types.map((t) => <span key={t} className="badge tone-muted" style={{ marginRight: 4 }}>{sourceLabel(t)}</span>)}</>;
}

function Reasons({ candidate, text }: { candidate: Candidate; text: SelectionView["reason_text"] }) {
  if (candidate.reasons.length === 0) return <span className="muted">—</span>;
  return <ul className="plain">{candidate.reasons.map((r) => <li key={r}>{text[r]}</li>)}</ul>;
}
