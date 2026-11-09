import { notFound } from "next/navigation";
import { PageHead, Stats } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { phone } from "@/lib/lineage";
import { counts, statusLabel, statusTone } from "@/lib/risk";

const STRIPE = { near_miss: "is-bad", unproven: "is-warn", missing: "", provable: "is-ok" } as const;

// The vendor risk list (spec 0026, owner Inesh), the demo's opening screen: is each vendor's number
// on file one the Trust Ledger can prove? Every explanation is the ledger's status_text.
export default async function RiskPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const vendors = await core.vendorRisk(datasetId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });

  const tones = { near_miss: "bad", unproven: "warn", missing: undefined, provable: "ok" } as const;

  return (
    <main>
      <PageHead title="Vendor risk" lead="Is each vendor's number on file one the Trust Ledger can prove? A near miss is one digit off, or two digits swapped, from a number it can prove." />
      <Stats label="Vendors by status" items={counts(vendors).map(([status, n]) => ({ label: cap(statusLabel(status)), value: n, tone: tones[status] }))} />
      {vendors.length === 0 ? (
        <section className="card empty"><p>The Trust Ledger has no documents for this dataset&apos;s vendors yet.</p></section>
      ) : (
        <section className="card flush" aria-label="Vendors">
          <div className="table-wrap">
            <table className="risk">
              <thead>
                <tr><th className="w-vendor">Vendor</th><th>Status</th><th>On file</th><th>Provable</th></tr>
              </thead>
              <tbody>
                {vendors.map((v) => (
                  <tr key={v.vendor_id} className={STRIPE[v.status]}>
                    <td><strong>{v.legal_name}</strong><div className="muted sub cell-note">{v.status_text}</div></td>
                    <td><span className={`badge ${statusTone(v.status)}`}>{statusLabel(v.status)}</span></td>
                    <td className="mono nowrap">{v.number_on_file ? phone(v.number_on_file) : "—"}</td>
                    <td className="mono nowrap">{v.provable_number ? phone(v.provable_number) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
