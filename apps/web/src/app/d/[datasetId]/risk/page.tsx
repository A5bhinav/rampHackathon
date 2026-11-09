import { notFound } from "next/navigation";
import { PageHead, Stats } from "@/components/page-head";
import { CoreError, core } from "@/lib/core";
import { phone } from "@/lib/lineage";
import { counts, statusLabel, statusTone } from "@/lib/risk";

const STRIPE = { near_miss: "is-bad", unproven: "is-warn", missing: "", provable: "is-ok" } as const;
const COUNT_LABEL = { near_miss: "Near miss", unproven: "Unproven", missing: "No number on file", provable: "Provable" } as const;

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
      <PageHead title="Vendor risk" lead="For each vendor, whether the phone number on file is one the Trust Ledger can prove belongs to them." />
      <Stats label="Vendors by status" items={counts(vendors).map(([status, n]) => ({ n, label: COUNT_LABEL[status], tone: statusTone(status) }))} />
      {vendors.length === 0 ? (
        <section className="card empty"><p>The Trust Ledger has no documents for this dataset&apos;s vendors yet.</p></section>
      ) : (
        <section className="card table-wrap flush" aria-label="Vendors">
          <table className="risk">
            <thead>
              <tr><th className="w-vendor">Vendor</th><th>Status</th><th>Number on file</th><th>Provable number</th></tr>
            </thead>
            <tbody>
              {vendors.map((v) => (
                <tr key={v.vendor_id} className={STRIPE[v.status]}>
                  <td>
                    <strong>{v.legal_name}</strong>
                    <div className="muted sub cell-note">{v.status_text}</div>
                  </td>
                  <td><span className={`badge ${statusTone(v.status)}`}>{statusLabel(v.status)}</span></td>
                  <td className="mono nowrap">{v.number_on_file ? phone(v.number_on_file) : <span className="muted">none</span>}</td>
                  <td className="mono nowrap">{v.provable_number ? phone(v.provable_number) : <span className="muted">none</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}
