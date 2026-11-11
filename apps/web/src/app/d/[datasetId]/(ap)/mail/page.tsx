import Link from "next/link";
import { notFound } from "next/navigation";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { flagLabel, outcomeLabel, replyLabel } from "@/lib/mail";
import { formatAccount } from "@/lib/ramp-portal";

// Mail (spec 0089): every inbound email the door has seen and what it did. There is no body to show
// (none is stored), and a number is routing plus last four only (ADR-8).
export default async function MailPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const items = await core.mail(datasetId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });
  return (
    <main>
      <header>
        <p className="section-marker">Email door</p>
        <h1>Mail</h1>
        <p className="muted">Emails sent to Dialback, newest first, and what was done with each. Email bodies are never kept.</p>
      </header>
      {items.length === 0 ? <p className="card-body muted sub">No mail has arrived yet.</p> : (
        <section className="card flush" aria-label="Inbound mail">
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Received</th><th>Sender</th><th>Subject</th><th>Bank change?</th><th>What happened</th><th>Reply</th><th>Red flags</th><th>Vendor</th><th>Payment details</th></tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.id}>
                    <td className="nowrap">{when(m.received_at)}</td>
                    <td className="mono">{m.sender}</td>
                    <td>{m.subject ?? <span className="muted">(no subject)</span>}</td>
                    <td>{m.flagged ? <span className="badge tone-warn">Flagged</span> : <span className="muted">No</span>}</td>
                    <td>
                      {outcomeLabel(m.outcome)}
                      {m.case_id ? <> <Link className="badge-link" href={`/d/${datasetId}/cases/${m.case_id}`}>Open case</Link></> : null}
                    </td>
                    <td>{replyLabel(m.reply)}</td>
                    <td>
                      {m.red_flags.length === 0 ? <span className="muted">None</span> : (
                        <span className="tags">{m.red_flags.map((f) => <span key={f} className="badge tone-bad" title={flagLabel(f)}>{flagLabel(f)}</span>)}</span>
                      )}
                    </td>
                    <td>{m.vendor ? m.vendor.legal_name : <span className="muted">Unknown</span>}</td>
                    <td className="mono nowrap">
                      {m.routing_number && m.last4 ? formatAccount({ routing_number: m.routing_number, last4: m.last4 }) : <span className="muted">None given</span>}
                    </td>
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
