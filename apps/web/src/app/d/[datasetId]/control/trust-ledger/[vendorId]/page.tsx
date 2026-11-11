import { notFound } from "next/navigation";
import { CandidatesTable, LaneFigure } from "@/components/lineage";
import { CardHead, PageHead } from "@/components/page";
import type { LedgerDocument } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { date, when } from "@/lib/format";
import { lanePoints, phone, sourceLabel } from "@/lib/lineage";
import { statusLabel, statusTone } from "@/lib/risk";
import { hiddenLabel, kindLabel, laneDates, numberKinds, type NumberKind } from "@/lib/vendor-ledger";

// A vendor's history in the Trust Ledger (spec 0104): the rule as of now, the first-seen chart, every
// number with the ledger's reasons, and the documents with the text each number was read from. Every
// explanation is the ledger's (status_text, reason_text). Document text is data, shown as plain text (R9).
export default async function VendorLedgerPage({ params }: { params: Promise<{ datasetId: string; vendorId: string }> }) {
  const { datasetId, vendorId } = await params;
  const v = await core.vendorLedger(datasetId, vendorId).catch((e) => {
    if (e instanceof CoreError && (e.status === 404 || e.status === 422)) notFound();  // 422: not a vendor id
    throw e;
  });
  const { risk, rule } = v;
  const chart = lanePoints(rule, laneDates(v.documents, rule.cutoff, v.document_count));  // each dot at its own source's date (D7)

  return (
    <main>
      <PageHead title={risk.legal_name}
        lead="Every number the Trust Ledger has seen for this vendor, what the rule makes of each as of now, and the documents it came from." />
      <section className="card" aria-labelledby="status-h">
        <CardHead marker="Status" title="As of now" id="status-h"
          meta={<span className={`badge ${statusTone(risk.status)}`}>{statusLabel(risk.status)}</span>} />
        <div className="card-body">
          <p>{risk.status_text}</p>
          <dl className="facts">
            <dt>On file</dt><dd className="mono">{risk.number_on_file ? phone(risk.number_on_file) : "none"}</dd>
            <dt>Provable</dt><dd className="mono">{risk.provable_number ? phone(risk.provable_number) : "none"}</dd>
          </dl>
        </div>
      </section>
      {chart ? <LaneFigure chart={chart} cutoff={rule.cutoff} what="the cutoff" cutoffName="the cutoff" cutoffLabel="Cutoff" /> : null}
      {rule.candidates.length ? (
        <CandidatesTable candidates={rule.candidates} chosen={rule.contact_point_id} cutoff={rule.cutoff}
          reasonText={v.reason_text} what="the cutoff" />
      ) : null}
      <dl className="rule-meta" aria-label="The rule as of now">
        <div><dt>Rule</dt><dd className="mono">{rule.rule_version}</dd></div>
        <div><dt>As of</dt><dd>{when(rule.as_of)}</dd></div>
        <div><dt>Cutoff</dt><dd>{when(rule.cutoff)}</dd></div>
        <div><dt>Minimum</dt><dd>{rule.rule_settings.min_source_types} source types, {rule.rule_settings.min_age_days} days</dd></div>
      </dl>
      <Documents documents={v.documents} total={v.document_count} kinds={numberKinds(rule)} />
    </main>
  );
}

const KIND = { pick: ["chosen", "tone-ok"], alternate: ["alternate", "tone-info"], refused: ["refused", "tone-bad"] } as const;

/** The vendor's documents, newest first, each email before its attachments, with every phone number
 *  found in each and the exact text it was read from. */
function Documents({ documents, total, kinds }: { documents: LedgerDocument[]; total: number; kinds: Map<string, NumberKind> }) {
  return (
    <section className="card flush" aria-labelledby="docs-h">
      <CardHead marker="Sources" title={<>Documents <span className="count">{total}</span></>} id="docs-h"
        meta={documents.length < total ? `newest ${documents.length}` : undefined} />
      {documents.length === 0 ? (
        <div className="card-body"><p className="muted">The Trust Ledger has no documents for this vendor yet.</p></div>
      ) : (
        <div className="table-wrap">
          <table className="candidates-table">
            <thead><tr><th>Received</th><th>Channel</th><th>Document</th><th>Numbers found</th></tr></thead>
            <tbody>
              {documents.map((d) => (
                <tr key={d.document_id}>
                  <td className="nowrap">{when(d.received_at)}</td>
                  <td><span className="badge tone-muted">{sourceLabel(d.custody_channel)}</span></td>
                  <td>
                    <strong>{kindLabel(d.kind)}</strong>{d.parent_document_id ? <span className="muted"> (attachment)</span> : null}
                    {d.subject ? <div>{d.subject}</div> : null}
                    {d.sender ? <div className="muted sub mono">{d.sender}</div> : null}
                    {d.claimed_date ? <div className="muted sub">Printed date {date(d.claimed_date)}</div> : null}
                  </td>
                  <td>
                    {d.observations.length === 0 ? <span className="muted">none</span> : d.observations.map((o, i) => {
                      const kind = kinds.get(o.e164);  // every listed row was read by the rule; no badge if not
                      const hidden = hiddenLabel(o.visibility);
                      return (
                        <div key={i} className="cell-note">
                          <span className="mono nowrap">{phone(o.e164)}</span>{" "}
                          {kind ? <span className={`badge ${KIND[kind][1]}`}>{KIND[kind][0]}</span> : null}
                          <span className="muted sub"> role {o.role}, party {o.party}{hidden ? `, ${hidden}` : ""}</span>
                          {o.quote ? <blockquote>{o.quote}</blockquote> : null}
                        </div>
                      );
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
