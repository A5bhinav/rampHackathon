import Link from "next/link";
import { OutcomeBadge, StateBadge } from "@/components/badges";
import { PaymentLanes, QueueRail } from "@/components/flow";
import { PageHead, Stats } from "@/components/page";
import type { CaseState, CaseSummary } from "@/lib/api-types";
import { core } from "@/lib/core";
import { stationCounts } from "@/lib/flow";
import { ago, brief, datasetNow, date, dueLabel, relative, when } from "@/lib/format";

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
      <PageHead title="Case queue" lead="Every vendor whose bank details changed in Ramp. Their payments stay on hold until the vendor confirms on a number Dialback can prove, and a different person releases them." />
      <QueueRail counts={stationCounts(cases)} />
      <Stats label="Held payments" items={[
        { label: "Bills on hold", value: cases.reduce((n, c) => n + c.bills_held, 0), tone: "info" },
        { label: "Waiting on a person", value: waiting.length, tone: "warn" },
        { label: "Payment overdue or due within 3 days", value: dueSoon, tone: "bad" },
        { label: "Hold not confirmed", value: unconfirmed, tone: "bad" },
      ]} />
      <PaymentLanes cases={cases} now={now} datasetId={datasetId} />
      {cases.length === 0 ? <div className="card empty"><p>No open cases. A case opens here when a vendor&apos;s bank details change in Ramp.</p></div> : null}
      {waiting.length > 0 ? <QueueTable title="Waiting on a person" cases={waiting} datasetId={datasetId} now={now} /> : null}
      {working.length > 0 ? <QueueTable title="In progress" cases={working} datasetId={datasetId} now={now} /> : null}
    </main>
  );
}

// A stripe on the vendor cell for what needs eyes first: an unconfirmed hold, a payment due soon, a block.
function stripe(c: CaseSummary, soon: boolean): string {
  if (c.hold_status === "failed" || soon || c.state === "blocked") return "is-bad";
  if (c.state === "needs_review") return "is-warn";
  if (c.state === "awaiting_release") return "is-ok";
  return "";
}

function QueueTable({ title, cases, datasetId, now }: { title: string; cases: CaseSummary[]; datasetId: string; now: Date }) {
  return (
    <section className="card queue-group" aria-label={title}>
      <h2>{title} <span className="count">{cases.length}</span></h2>
      <div className="table-wrap">
      <table>
        <thead>
          <tr><th className="w-vendor">Vendor</th><th>State</th><th>Decision</th><th className="num">Held bills</th><th>Next payment</th><th>Opened</th><th>Deadline</th></tr>
        </thead>
        <tbody>
          {cases.map((c) => {
            const due = dueLabel(c.next_payment_date, now);
            return (
              <tr key={c.id} className={stripe(c, Boolean(due?.soon))}>
                <td>
                  <Link href={`/d/${datasetId}/cases/${c.id}`}>{c.vendor.legal_name}</Link>
                  {c.unknown_changer ? <div className="cell-note"><span className="badge tone-warn">two approvers</span></div> : null}
                </td>
                <td><StateBadge state={c.state} />{c.version > 1 ? <div className="muted sub cell-note">change {c.version}</div> : null}</td>
                <td><OutcomeBadge outcome={c.outcome} /></td>
                <td className="num">{c.hold_status === "failed" ? <span className="badge tone-bad">hold not confirmed</span> : c.bills_held}</td>
                <td>
                  {date(c.next_payment_date)}
                  {due ? <div className="cell-note"><span className={`badge ${due.soon ? "tone-bad" : "tone-muted"}`}>{due.text}</span></div> : null}
                </td>
                <td className="nowrap"><span title={when(c.opened_at)}>{brief(c.opened_at)}</span><div className="muted sub">{ago(c.opened_at, now)}</div></td>
                <td className="nowrap">
                  <span title={when(c.deadline_at)}>{brief(c.deadline_at) || "—"}</span>
                  {c.deadline_at && c.state !== "releasing" ? (  // as on the case page: no countdown once a release is under way
                    <div className="muted sub">{Date.parse(c.deadline_at) < now.getTime() ? "passed" : relative(c.deadline_at, now)}</div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </section>
  );
}
