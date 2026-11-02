import Link from "next/link";
import { OutcomeBadge, StateBadge } from "@/components/badges";
import { core } from "@/lib/core";
import { date, when } from "@/lib/format";

// Case queue: unfinished cases by state and age, and held bills near their due date.
export default async function CaseQueue({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const cases = await core.cases(datasetId);
  return (
    <main>
      <h1>Case queue</h1>
      <p className="muted">Every vendor whose bank details changed. Payments stay on hold until a different person releases them.</p>
      <div className="card table-wrap">
        {cases.length === 0 ? (
          <p className="muted">No open cases.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Vendor</th><th>State</th><th>Decision</th><th>Opened</th><th>Held bills</th><th>Next payment date</th><th>Deadline</th></tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/d/${datasetId}/cases/${c.id}`}>{c.vendor.legal_name}</Link>
                    {c.unknown_changer ? <div><span className="badge tone-warn">two approvers</span></div> : null}
                  </td>
                  <td><StateBadge state={c.state} /></td>
                  <td><OutcomeBadge outcome={c.outcome} /></td>
                  <td>{when(c.opened_at)}</td>
                  <td>{c.hold_status === "failed" ? <span className="badge tone-bad">hold not confirmed</span> : c.bills_held}</td>
                  <td>{date(c.next_payment_date)}</td>
                  <td>{when(c.deadline_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
