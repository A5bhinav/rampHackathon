import Link from "next/link";
import { notFound } from "next/navigation";
import { StateBadge } from "@/components/badges";
import { PageHead } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { formatAccount } from "@/lib/ramp-portal";

// Ramp (mock) (spec 0077): a stand-in for Ramp Bill Pay's vendor list, where the demo's clerk edits a
// vendor's payment details. The change goes to the Ramp mock; Dialback then finds it like any change
// made in Ramp. Routing number and last four only (ADR-8).
export default async function RampVendors({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const [data, cases] = await Promise.all([
    core.vendorAccounts(datasetId).catch((e) => {
      if (e instanceof CoreError && e.status === 404) notFound();
      throw e;
    }),
    core.cases(datasetId),
  ]);
  const stateOf = new Map(cases.map((c) => [c.id, c.state]));
  return (
    <main className="ramp">
      <PageHead title="Ramp (mock) vendors"
        lead="A stand-in for Ramp Bill Pay: edit a vendor's payment details the way a clerk would. Dialback catches the change." />
      {!data.can_change && data.reason ? <div className="callout"><div className="callout-body"><p>{data.reason}</p></div></div> : null}
      <section className="card flush" aria-label="Vendors in Ramp (mock)">
        <div className="table-wrap">
          <table>
            <thead><tr><th className="w-vendor">Vendor</th><th>Payment details</th><th>In Dialback</th><th className="num"><span className="sr-only">Edit</span></th></tr></thead>
            <tbody>
              {data.vendors.map((v) => {
                const state = v.open_case_id ? stateOf.get(v.open_case_id) : undefined;
                return (
                  <tr key={v.vendor_id}>
                    <td><strong className="vendor-name">{v.legal_name}</strong></td>
                    <td className="mono nowrap">{formatAccount(v.account)}</td>
                    <td>{v.open_case_id ? <Link className="badge-link" href={`/d/${datasetId}/cases/${v.open_case_id}`}>{state ? <StateBadge state={state} /> : "Open case"}</Link> : <span className="muted">—</span>}</td>
                    <td className="num">{data.can_change ? <Link className="button-link" href={`/d/${datasetId}/ramp/${v.vendor_id}`}>Edit payment details</Link> : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data.vendors.length === 0 ? <p className="card-body muted sub">This demo has no vendors yet.</p> : null}
      </section>
    </main>
  );
}
