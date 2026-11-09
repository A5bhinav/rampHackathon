import { notFound } from "next/navigation";
import { CoreError, core } from "@/lib/core";
import { phone } from "@/lib/lineage";
import { counts, statusLabel, statusTone } from "@/lib/risk";

// The vendor risk list (spec 0026, owner Inesh), the demo's opening screen: is each vendor's number
// on file one the Trust Ledger can prove? Every explanation is the ledger's status_text.
export default async function RiskPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const vendors = await core.vendorRisk(datasetId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });

  return (
    <main>
      <h1>Vendor risk</h1>
      <p style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {counts(vendors).map(([status, n]) => (
          <span key={status} className={`badge ${n ? statusTone(status) : "tone-muted"}`}>{n} {statusLabel(status)}</span>
        ))}
      </p>
      {vendors.length === 0 ? (
        <section className="card"><p className="muted">The Trust Ledger has no documents for this dataset&apos;s vendors yet.</p></section>
      ) : (
        <section className="card" aria-label="Vendors">
          <ul className="plain" style={{ listStyle: "none", paddingLeft: 0, margin: 0 }}>
            {vendors.map((v) => (
              <li key={v.vendor_id} style={{ padding: "12px 0", borderBottom: "1px solid var(--line)" }}>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <strong>{v.legal_name}</strong>
                  <span className={`badge ${statusTone(v.status)}`}>{statusLabel(v.status)}</span>
                </div>
                <dl className="facts" style={{ marginTop: 6 }}>
                  <dt>On file</dt><dd className="mono">{v.number_on_file ? phone(v.number_on_file) : "—"}</dd>
                  <dt>Provable</dt><dd className="mono">{v.provable_number ? phone(v.provable_number) : "—"}</dd>
                </dl>
                <p className="muted" style={{ margin: "4px 0 0" }}>{v.status_text}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
