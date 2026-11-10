import Link from "next/link";
import { notFound } from "next/navigation";
import { StateBadge } from "@/components/badges";
import { PageHead } from "@/components/page";
import type { VendorRisk } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { phone } from "@/lib/lineage";
import { statusLabel, statusTone } from "@/lib/risk";

// Vendors (spec 0063): every vendor the Trust Ledger knows on this dataset, with its number on
// file, whether that number is provable (the ledger's own status and sentence), and its open case.
export default async function VendorsPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const [risk, open, datasets] = await Promise.all([
    core.vendorRisk(datasetId).catch((e): VendorRisk[] | null => {
      if (e instanceof CoreError && e.status === 404) notFound();
      return null;  // the ledger couldn't be read: list vendors from their cases and say so
    }),
    core.cases(datasetId),
    core.datasets(),
  ]);
  const dataset = datasets.find((d) => d.id === datasetId);
  // A vendor with several open cases: its newest one.
  const caseOf = new Map([...open].sort((a, b) => a.opened_at.localeCompare(b.opened_at)).map((c) => [c.vendor.id, c]));
  const vendors = risk ?? [];
  // Vendors the ledger knows, plus any vendor with an open case the ledger has no documents for.
  const known = new Set(vendors.map((v) => v.vendor_id));
  const rows = [
    ...vendors.map((v) => ({ id: v.vendor_id, name: v.legal_name, risk: v })),
    ...open.filter((c) => !known.has(c.vendor.id)).map((c) => ({ id: c.vendor.id, name: c.vendor.legal_name, risk: null })),
  ].filter((r, i, all) => all.findIndex((x) => x.id === r.id) === i);
  const sorted = rows.sort((a, b) => a.name.localeCompare(b.name));
  return (
    <main>
      <PageHead eyebrow={`${dataset?.label ?? "Dataset"} · Accounts payable`} title="Vendors"
        lead={`${sorted.length} vendor${sorted.length === 1 ? "" : "s"}; ${caseOf.size} with an open case.`} />
      {sorted.length === 0 ? (
        <div className="empty-card"><p>The Trust Ledger has no documents for this dataset&apos;s vendors yet.</p></div>
      ) : (
        <section className="card flush" aria-label="Vendors">
          <div className="table-wrap">
            <table className="risk">
              <thead><tr><th className="w-vendor">Vendor</th><th>Number on file</th><th>Trust Ledger</th><th>Open case</th></tr></thead>
              <tbody>
                {sorted.map((v) => {
                  const c = caseOf.get(v.id);
                  return (
                    <tr key={v.id}>
                      <td><strong className="vendor-name">{v.name}</strong>{v.risk ? <div className="muted sub cell-note">{v.risk.status_text}</div> : null}</td>
                      <td className="mono nowrap">{v.risk?.number_on_file ? phone(v.risk.number_on_file) : "—"}</td>
                      <td>{v.risk ? <span className={`badge ${statusTone(v.risk.status)}`}>{statusLabel(v.risk.status)}</span> : <span className="badge tone-muted">{risk === null ? "not reported yet" : "no Trust Ledger documents"}</span>}</td>
                      <td>{c ? <Link className="badge-link" href={`/d/${datasetId}/cases/${c.id}`}><StateBadge state={c.state} /></Link> : <span className="muted">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {risk === null ? <p className="muted sub">The Trust Ledger is not reported yet for this dataset.</p> : null}
      <p className="muted sub"><Link href={`/d/${datasetId}/control/trust-ledger`}>The Trust Ledger</Link> shows every vendor by risk, worst first.</p>
    </main>
  );
}
