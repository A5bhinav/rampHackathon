import Link from "next/link";
import { OutcomeBadge, StateBadge } from "@/components/badges";
import { PageHead, Stats } from "@/components/page-head";
import type { CaseState, CaseSummary } from "@/lib/api-types";
import { core } from "@/lib/core";
import { ago, datasetNow, date, dueLabel, relative, when } from "@/lib/format";

// Case queue: unfinished cases by state and age, and held bills near their due date (design,
// "Dashboard"). Cases waiting on a person come first, soonest saved payment date first, then
// the ones code is still working on. Times are read against the dataset's own clock, so a lab
// dataset's simulated time counts days the same way the deadline does.
const NEEDS_A_PERSON: CaseState[] = ["needs_review", "awaiting_release", "blocked"];

function bySoonestPayment(a: CaseSummary, b: CaseSummary): number {
  const ka = a.next_payment_date ?? "9999-12-31";
  const kb = b.next_payment_date ?? "9999-12-31";
  return ka === kb ? a.opened_at.localeCompare(b.opened_at) : ka.localeCompare(kb);
}

export default async function CaseQueue({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const [cases, datasets] = await Promise.all([core.cases(datasetId), core.datasets()]);
  const now = datasetNow(datasets.find((d) => d.id === datasetId)?.sim_now);
  const waiting = cases.filter((c) => NEEDS_A_PERSON.includes(c.state)).sort(bySoonestPayment);
  const working = cases.filter((c) => !NEEDS_A_PERSON.includes(c.state)).sort(bySoonestPayment);
  const dueSoon = cases.filter((c) => dueLabel(c.next_payment_date, now)?.soon).length;
  const unconfirmed = cases.filter((c) => c.hold_status === "failed").length;

  return (
    <main>
      <PageHead
        title="Case queue"
        lead="Every vendor whose bank details changed. Payments stay on hold until a different person releases them."
      />
      <Stats label="Summary" items={[
        { n: waiting.length, label: "Waiting on a person", tone: "tone-warn" },
        { n: working.length, label: "In progress", tone: "tone-info" },
        { n: dueSoon, label: "Payment due within 3 days or overdue", tone: "tone-bad" },
        { n: unconfirmed, label: "Hold not confirmed", tone: "tone-bad" },
      ]} />
      {cases.length === 0 ? <div className="card empty"><p>No open cases. A case opens here when a vendor&apos;s bank details change in Ramp.</p></div> : null}
      {waiting.length > 0 ? <QueueTable title="Waiting on a person" cases={waiting} datasetId={datasetId} now={now} /> : null}
      {working.length > 0 ? <QueueTable title="In progress" cases={working} datasetId={datasetId} now={now} /> : null}
    </main>
  );
}

function QueueTable({ title, cases, datasetId, now }: { title: string; cases: CaseSummary[]; datasetId: string; now: Date }) {
  return (
    <section className="card table-wrap queue-group" aria-label={title}>
      <h2>{title} <span className="count">{cases.length}</span></h2>
      <table>
        <thead>
          <tr><th className="w-vendor">Vendor</th><th>State</th><th>Decision</th><th className="num">Held bills</th><th>Next payment</th><th>Opened</th><th>Deadline</th></tr>
        </thead>
        <tbody>
          {cases.map((c) => {
            const due = dueLabel(c.next_payment_date, now);
            return (
              <tr key={c.id}>
                <td>
                  <Link href={`/d/${datasetId}/cases/${c.id}`}>{c.vendor.legal_name}</Link>
                  {c.unknown_changer ? <div className="cell-note"><span className="badge tone-warn">two approvers</span></div> : null}
                </td>
                <td><StateBadge state={c.state} />{c.version > 1 ? <div className="muted sub">change {c.version}</div> : null}</td>
                <td><OutcomeBadge outcome={c.outcome} /></td>
                <td className="num">{c.hold_status === "failed" ? <span className="badge tone-bad">hold not confirmed</span> : c.bills_held}</td>
                <td>
                  {date(c.next_payment_date)}
                  {due ? <div className="cell-note"><span className={`badge ${due.soon ? "tone-bad" : "tone-muted"}`}>{due.text}</span></div> : null}
                </td>
                <td>{when(c.opened_at)}<div className="muted sub">{ago(c.opened_at, now)}</div></td>
                <td>
                  {when(c.deadline_at)}
                  {c.deadline_at && c.state !== "releasing" ? (  // as on the case page: no countdown once a release is under way
                    <div className="muted sub">{Date.parse(c.deadline_at) < now.getTime() ? "passed" : relative(c.deadline_at, now)}</div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
