import Link from "next/link";
import { OutcomeBadge } from "@/components/badges";
import { PaymentLanes, QueueRail } from "@/components/flow";
import { NEEDS_A_PERSON, QueueTable, bySoonestPayment } from "@/components/queue";
import { Callout, PageHead, Stats } from "@/components/page";
import { RingMotif } from "@/components/ring-band";
import type { EvidenceLog } from "@/lib/api-types";
import { core } from "@/lib/core";
import { stationCounts, stationsFromEvidence } from "@/lib/stations";
import { heldTotals } from "@/lib/overview";
import { demoStory } from "@/lib/story";
import { ago, brief, datasetNow, dueLabel, when, words } from "@/lib/format";

// The inbox (case queue; specs 0009, 0041): unfinished cases by state and age, and held bills
// near their due date. Cases waiting on a person come first, soonest saved payment date first,
// then the ones code is still working on. Times are read against the dataset's own clock, so a
// lab dataset's simulated time counts days the same way the deadline does.
export default async function CaseQueue({ params, searchParams }: {
  params: Promise<{ datasetId: string }>;
  searchParams: Promise<{ missing?: string }>;
}) {
  const { datasetId } = await params;
  const asked = demoStory((await searchParams).missing ?? "");
  const [cases, datasets, all, log] = await Promise.all([
    core.cases(datasetId), core.datasets(), core.allCases(datasetId),
    core.evidence(datasetId).catch((): EvidenceLog | null => null),  // the feed never takes the queue down
  ]);
  const dataset = datasets.find((d) => d.id === datasetId);
  const released = all.filter((c) => c.state === "released").sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const now = datasetNow(dataset?.sim_now);
  const waiting = cases.filter((c) => NEEDS_A_PERSON.includes(c.state)).sort(bySoonestPayment);
  const working = cases.filter((c) => !NEEDS_A_PERSON.includes(c.state)).sort(bySoonestPayment);
  const soon = cases.filter((c) => dueLabel(c.next_payment_date, now)?.soon).sort(bySoonestPayment);
  const unconfirmed = cases.filter((c) => c.hold_status === "failed").length;
  const held = heldTotals(cases);
  const missing = asked && !cases.some((c) => c.vendor.legal_name === asked.legal_name) ? asked : undefined;
  const rows = log?.events ?? [];  // without the log, each case is placed by its state alone
  const feed = rows.slice(0, 6);
  const rails = new Map(cases.map((c) => [c.id, stationsFromEvidence(rows, c)]));

  return (
    <main className="inbox">
      <PageHead
        eyebrow={`${dataset?.label ?? "Dataset"} · Accounts payable`}
        title="Inbox"
        lead={`${waiting.length} need${waiting.length === 1 ? "s" : ""} you. ${held.bills} bill${held.bills === 1 ? " is" : "s are"} held across ${held.vendors} vendor${held.vendors === 1 ? "" : "s"}.`}
      />
      {missing ? (
        <Callout role="status">
          <p>
            {missing.legal_name} has no case on this dataset yet. Its case opens when the team makes the demo&apos;s bank change for it
            (<span className="mono">dialback-core demo change --vendor {missing.ref}</span>).
          </p>
        </Callout>
      ) : null}
      <Stats label="Held payments" items={[
        { label: "On hold", value: held.bills, sub: `${held.bills === 1 ? "bill" : "bills"} · ${held.vendors} vendor${held.vendors === 1 ? "" : "s"}` },
        { label: "Needs you", value: waiting.length, tone: "warn", sub: "cases waiting on a person" },
        { label: "Due ≤ 3 days", value: soon.length, tone: "bad",
          sub: soon.length ? soon.slice(0, 3).map((c) => c.vendor.legal_name).join(" · ") : "payments overdue or due soon" },
        { label: "Hold not confirmed", value: unconfirmed, tone: "bad" },
      ]} />
      <QueueRail counts={stationCounts(cases, rows)} />

      <div className="tabs-row">
        <nav className="tabs" aria-label="Queue sections">
          <a href="#waiting" className="tab tab-waiting">Needs you <span className="tab-n">{waiting.length}</span></a>
          <a href="#working" className="tab tab-working">In progress <span className="tab-n">{working.length}</span></a>
          <a href="#released" className="tab tab-released">Released <span className="tab-n">{released.length}</span></a>
        </nav>
        <span className="section-marker">Sorted by soonest payment</span>
      </div>

      {cases.length === 0 ? (
        <div className="card empty-card">
          <RingMotif />
          <p>No open cases. A case opens here when a vendor&apos;s bank details change in Ramp.{released.length ? " Released changes are listed below." : ""}</p>
        </div>
      ) : null}
      {waiting.length > 0 ? <QueueTable id="waiting" title="Waiting on a person" cases={waiting} datasetId={datasetId} now={now} rails={rails} /> : null}
      {working.length > 0 ? <QueueTable id="working" title="In progress" cases={working} datasetId={datasetId} now={now} rails={rails} /> : null}

      <div className="inbox-lower">
        <div className="inbox-lanes"><PaymentLanes cases={cases} now={now} datasetId={datasetId} /></div>
        <section className="card activity" aria-labelledby="activity-h">
          <div className="card-head">
            <span className="section-marker">Activity</span>
            <h2 id="activity-h">Latest evidence</h2>
            <span className="card-meta"><Link href={`/d/${datasetId}/control/evidence`}>Evidence</Link></span>
          </div>
          {feed.length ? (
            <ol className="feed">
              {feed.map((e) => (
                <li key={e.seq}>
                  <span className="mono feed-time" title={when(e.occurred_at)}>{brief(e.occurred_at)}</span>
                  <span>
                    {e.vendor && e.case_id ? <Link href={`/d/${datasetId}/cases/${e.case_id}`}>{e.vendor}</Link> : e.vendor ? <strong>{e.vendor}</strong> : null}
                    {e.vendor ? " · " : ""}{words(e.type)}
                  </span>
                </li>
              ))}
            </ol>
          ) : <p className="card-body muted sub">No evidence rows yet.</p>}
        </section>
      </div>

      <section id="released" className="card queue-group" aria-labelledby="released-h">
        <div className="card-head">
          <span className="section-marker">Done</span>
          <h2 id="released-h">Released <span className="count">{released.length}</span></h2>
          <span className="card-meta muted sub">The hold is off; nothing is left to decide</span>
        </div>
        {released.length > 0 ? (
          <ul className="released">
            {released.map((c) => (
              <li key={c.id}>
                <Link href={`/d/${datasetId}/cases/${c.id}`}>{c.vendor.legal_name}</Link>
                <OutcomeBadge outcome={c.outcome} />
                <span className="muted sub" title={when(c.updated_at)}>released {ago(c.updated_at, now)}</span>
              </li>
            ))}
          </ul>
        ) : <p className="card-body muted sub">Nothing released yet.</p>}
      </section>
    </main>
  );
}
