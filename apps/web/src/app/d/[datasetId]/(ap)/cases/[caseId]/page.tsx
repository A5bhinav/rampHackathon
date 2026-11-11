import Link from "next/link";
import { notFound } from "next/navigation";
import { ChainBadge, OutcomeBadge, StateBadge, outcomeOf } from "@/components/badges";
import { CallBody } from "@/components/call";
import { DecisionConsole } from "@/components/console";
import { DecisionDetail } from "@/components/decision";
import { AutoRefresh } from "@/components/auto-refresh";
import { CaseRail } from "@/components/flow";
import { Icon } from "@/components/icons";
import { PaymentPath } from "@/components/path";
import { Callout, FoldCard, PageHead } from "@/components/page";
import type { Alert, CaseDetail, Candidate, SelectionView } from "@/lib/api-types";
import { AlertBanners } from "@/components/alerts";
import { alertsForCase } from "@/lib/alerts";
import { CoreError, core } from "@/lib/core";
import { brief, datasetNow, date, dueLabel, relative, when, words } from "@/lib/format";
import { phone, seenBefore, sourceLabel, split } from "@/lib/lineage";
import { REFRESH_STATES, stations } from "@/lib/stations";
import { canWatchCall } from "@/lib/live-call";
import { stateLabel } from "@/lib/states";
import { attemptRows, changeLine, decisionLine, holdLine, numberLine, openCard, trailLine } from "@/lib/case-cards";
import { releaseAction, reviewAction } from "./actions";

// Case detail and the action rail (specs 0009, 0036, 0041): the change, the hold, the number
// chosen, the call, the decision, the evidence trail and (only for someone the rules allow)
// release or review. The database refuses the changer whatever this page shows.
const NOTICES: Record<string, string> = {
  applied: "Done.",
  already_applied: "That was already recorded.",
  awaiting_second_approver: "Your approval is recorded. A second approver must release this change.",
};

const account = (a: { routing_number: string; last4: string }) => `${a.routing_number} ····${a.last4}`;

export default async function CaseDetailPage({ params, searchParams }: {
  params: Promise<{ datasetId: string; caseId: string }>;
  searchParams: Promise<{ done?: string; to?: string }>;
}) {
  const [{ datasetId, caseId }, { done, to }] = await Promise.all([params, searchParams]);
  const [detail, datasets, view, alerts] = await Promise.all([
    core.case(caseId).catch((e) => {
      if (e instanceof CoreError && e.status === 404) notFound();
      throw e;
    }),
    core.datasets(),
    core.selection(caseId).catch((): SelectionView | null => null),  // the number card says nothing rather than failing the page
    core.alerts(datasetId).catch((): Alert[] => []),  // this case's alerts stay full banners here (spec 0066)
  ]);
  const { case: c, change, hold, decision, permissions: can } = detail;
  const path = `/d/${datasetId}/cases/${caseId}`;
  const now = datasetNow(datasets.find((d) => d.id === datasetId)?.sim_now);
  const deadlinePassed = c.deadline_at ? Date.parse(c.deadline_at) < now.getTime() : false;
  // ADR-10: with no known changer, the first approval for this version waits for a second approver.
  const firstApproval = c.unknown_changer && c.state === "awaiting_release"
    ? detail.releases.find((r) => r.action === "approve" && r.case_version === c.version) : undefined;
  const lastStep = detail.timeline.at(-1);  // its summary is the core's template, so the reason is never guessed here
  const nextDue = dueLabel(c.next_payment_date, now);

  const steps = stations(detail.timeline, c.state, decision?.outcome ?? null);
  const open = openCard(steps);  // the card for the station the case is at starts open; the rest start closed
  const attempts = attemptRows(detail.calls ?? []);

  return (
    <main className="case-page">
      <AlertBanners alerts={alertsForCase(alerts, caseId)} datasetId={datasetId} />
      <PageHead
        title={c.vendor.legal_name}
        aside={
          <span className="present-links">
            {canWatchCall(detail.timeline) ? <Link className="button-link primary" href={`${path}/call`}>Watch the call</Link> : null}
            <Link className="button-link" href={`${path}/flow`}>Flow mode</Link>
          </span>
        }
      >
        <span className="tags">
          <StateBadge state={c.state} />
          <Link className="badge-link" href={`/d/${datasetId}/control/evidence${detail.chain.ok ? "" : `#row-${detail.chain.first_bad_seq}`}`}>
            <ChainBadge chain={detail.chain} />
          </Link>
          {c.unknown_changer ? <span className="badge tone-warn">Unknown changer: two approvers</span> : null}
          {c.deadline_at && !["released", "releasing"].includes(c.state) ? (
            <span className={`badge ${deadlinePassed ? "tone-bad" : "tone-muted"}`} title={when(c.deadline_at)}>
              Deadline {deadlinePassed ? "passed" : relative(c.deadline_at, now)}
            </span>
          ) : null}
          <span className="muted sub">
            <strong className="held-n">{c.bills_held}</strong> {c.bills_held === 1 ? "bill" : "bills"} held
            {c.next_payment_date ? <> · next payment {date(c.next_payment_date)}{nextDue ? ` · ${nextDue.text}` : ""}</> : null}
            {" "}· version {c.version} · via {change.detected_via}
          </span>
        </span>
      </PageHead>

      {done && NOTICES[done] ? (
        <Callout tone="ok" role="status" className="page-notice">
          <p>{NOTICES[done]}{to ? ` The case moved to ${stateLabel(to).toLowerCase()}.` : ""}</p>
        </Callout>
      ) : null}

      <CaseRail steps={steps} />
      <AutoRefresh active={REFRESH_STATES.has(c.state)} />

      <div className="case-grid">
        <div className="case-main">
          <FoldCard id="decision-h" marker="05" title="Decision" open={open === "decision"}
            line={decisionLine(decision, decision ? outcomeOf(decision.outcome).label : "") ?? "No decision yet"}
            meta={decision ? <OutcomeBadge outcome={decision.outcome} /> : null}>
            <div className="card-body">
              {decision ? <DecisionDetail decision={decision} /> : (
                <p className="muted">
                  {["needs_review", "blocked"].includes(c.state) && lastStep
                    ? <>Code made no decision for this version. The last step: {lastStep.summary}</>
                    : "No decision yet for this version. Code decides once the call's answers are in; until then the hold stays on."}
                </p>
              )}
            </div>
          </FoldCard>

          <FoldCard id="change-h" marker="01" title="Change" open={open === "change"} line={changeLine(change)}>
            <div className="card-body">
              <PaymentPath
                bills={c.bills_held}
                nextPayment={c.next_payment_date}
                holdStatus={hold?.status ?? null}
                newAccount={account(change.new_account)}
                oldAccount={change.old_account ? account(change.old_account) : null}
              />
              <dl className="facts">
                <dt>Detected</dt><dd>{when(change.detected_at)} <span className="muted">via {change.detected_via}</span></dd>
                <dt>Entered by</dt><dd>{change.changed_by?.display_name ?? "Unknown (vendor-supplied or synced)"}</dd>
              </dl>
            </div>
          </FoldCard>

          <HoldCard detail={detail} now={now} open={open === "hold"} />
          <NumberCard view={view} path={path} open={open === "number"} />

          {attempts.length ? (
            <FoldCard id="call-h" marker="04" title="Call" open={open === "call"} line={attempts.find((a) => a.expanded)?.line}
              meta={<span className="badge tone-muted">AI agent: collects answers only</span>}>
              {attempts.some((a) => !a.expanded) ? (
                <ul className="attempt-lines" aria-label="Earlier attempts">
                  {attempts.filter((a) => !a.expanded).map((a) => <li key={a.call.call_id}>{a.line}{a.call.reason ? <span className="muted"> · {a.call.reason}</span> : null}</li>)}
                </ul>
              ) : null}
              {attempts.filter((a) => a.expanded).map((a) => (
                <CallBody key={a.call.call_id} call={a.call} script={detail.call_script} decision={decision} watchHref={canWatchCall(detail.timeline) ? `${path}/call` : undefined} />
              ))}
            </FoldCard>
          ) : detail.calls && c.state !== "open" ? (
            <FoldCard id="call-h" marker="04" title="Call" open={open === "call"} line="No call placed">
              <div className="card-body">
                <p className="quiet-fill">No call has been placed for this case.{lastStep ? <> The last step: {lastStep.summary}</> : null}</p>
              </div>
            </FoldCard>
          ) : null}

          <FoldCard id="timeline-h" marker="Record" title="Evidence trail" className="flush" line={trailLine(detail.timeline.length, detail.chain)}>
            <p className="card-body fold-links"><Link href={`/d/${datasetId}/control/evidence`}>Open in Evidence</Link></p>
            <div className="table-wrap">
              <table className="trail stack-sm">
                <thead><tr><th className="num">#</th><th>Event</th><th>State</th><th>By</th><th>Time</th><th>Hash</th></tr></thead>
                <tbody>
                  {detail.timeline.map((e) => (
                    <tr key={e.seq}>
                      <td className="num mono muted" data-label="#">{e.seq}</td>
                      <td data-label="Event">{e.summary}</td>
                      <td className="nowrap" data-label="State">{e.from_state ? `${stateLabel(e.from_state)} → ` : ""}{stateLabel(e.to_state)}</td>
                      <td data-label="By">{e.actor.display_name}</td>
                      <td className="nowrap" data-label="Time" title={when(e.occurred_at)}>{brief(e.occurred_at)}</td>
                      <td className="mono muted" data-label="Hash" title={e.hash}>{e.hash.slice(0, 12)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </FoldCard>
        </div>

        <aside className="case-actions" aria-label="Actions">
          <ActionRail detail={detail} path={path} datasetId={datasetId} firstApproval={firstApproval} lastStepSummary={lastStep?.summary} />
        </aside>
      </div>
    </main>
  );
}

function HoldCard({ detail, now, open }: { detail: CaseDetail; now: Date; open: boolean }) {
  const hold = detail.hold;
  const meta = !hold ? <span className="badge tone-muted">Placing</span>
    : hold.status === "failed" ? <span className="badge tone-bad">Not confirmed</span>
    : hold.confirmed_at ? <span className="badge tone-ok">Confirmed {brief(hold.confirmed_at)}</span>
    : <span className="badge tone-muted">{words(hold.status)}</span>;
  return (
    <FoldCard id="hold-h" marker="02" title="Hold" meta={meta} open={open} line={holdLine(hold, detail.case.bills_held)}>
      {hold ? (
        <>
          {hold.schedule.length > 0 ? (
            <div className="table-wrap">
              <table className="schedule stack-sm">
                <thead><tr><th>Bill</th><th>Saved payment date</th><th>Due</th><th>Status</th></tr></thead>
                <tbody>
                  {hold.schedule.map((s) => {
                    const due = hold.released_at ? null : dueLabel(s.payment_date, now);
                    return (
                      <tr key={s.bill_id}>
                        <td className="mono" data-label="Bill" title={s.bill_id}>{s.bill_id.slice(0, 8)}</td>
                        <td data-label="Saved payment date">{date(s.payment_date)}</td>
                        <td data-label="Due">{due ? <span className={`badge ${due.soon ? "tone-bad" : "tone-muted"}`}>{due.text}</span> : <span className="muted">—</span>}</td>
                        <td data-label="Status">{hold.released_at ? <span className="badge tone-ok">Restored</span> : <span className="badge tone-info">On hold</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          <div className="card-body">
            <dl className="facts">
              <dt>Confirmed</dt><dd>{when(hold.confirmed_at)}</dd>
              <dt>Released</dt><dd>{when(hold.released_at)}</dd>
              {hold.ramp_ref ? <><dt>Ramp reference</dt><dd className="mono">{hold.ramp_ref}</dd></> : null}
            </dl>
            {hold.last_error ? <Callout tone="bad" icon={<Icon name="alert" size={16} />}><p>{hold.last_error}</p></Callout> : null}
          </div>
        </>
      ) : (
        <div className="card-body">
          <p className="muted">
            Dialback is placing the hold in Ramp and will read it back before doing anything else. If Ramp won&apos;t confirm
            it, the case goes to a person and an alert is raised.
          </p>
        </div>
      )}
    </FoldCard>
  );
}

function NumberCard({ view, path, open }: { view: SelectionView | null; path: string; open: boolean }) {
  if (!view) return null;  // the selection couldn't be read: the lineage page still can
  const s = view.selection;
  const pick = s ? split(s).pick : null;
  return (
    <FoldCard id="number-h" marker="03" title="Number chosen" open={open} line={numberLine(view)}>
      <div className="card-body">
        <p className="fold-links"><Link href={`${path}/lineage`}>View lineage →</Link></p>
        {!s ? <p className="muted">{view.notes.no_selection}</p> : !pick ? (
          <>
            <p><span className="badge tone-bad">No provable number</span></p>
            <p>{s.reason}</p>
            <p className="muted sub">{view.notes.no_provable_number}</p>
          </>
        ) : (
          <div className="number-chosen">
            <div>
              <span className="muted sub">Called</span>
              <p className="phone-figure mono">{phone(pick.e164)}</p>
            </div>
            <dl className="facts">
              <dt>Seen in</dt><dd className="badges"><Sources candidate={pick} /></dd>
              <dt>First seen</dt><dd title={when(pick.first_seen_at)}>{seenBefore(pick.first_seen_at, s.cutoff)}</dd>
              <dt>Rule</dt><dd className="mono">{s.rule_version}</dd>
            </dl>
          </div>
        )}
      </div>
    </FoldCard>
  );
}

function Sources({ candidate }: { candidate: Candidate }) {
  if (candidate.source_types.length === 0) return <span className="muted">no independent source</span>;
  return <>{candidate.source_types.map((t) => <span key={t} className="badge tone-muted">{sourceLabel(t)}</span>)}</>;
}

function ActionRail({ detail, path, datasetId, firstApproval, lastStepSummary }: {
  detail: CaseDetail; path: string; datasetId: string; firstApproval: CaseDetail["releases"][number] | undefined; lastStepSummary?: string;
}) {
  const { case: c, change, decision, permissions: can, hold } = detail;
  const consoles = can.can_approve || can.can_reject || can.can_review;
  const need = c.unknown_changer ? 2 : 1;
  const approvals = detail.releases.filter((r) => r.action === "approve" && r.case_version === c.version).length;
  const releasedBy = detail.releases.find((r) => r.action === "approve" && r.case_version === c.version);
  const bills = `${c.bills_held} ${c.bills_held === 1 ? "bill" : "bills"}`;
  return (
    <>
      {firstApproval ? (
        <Callout tone="ok" role="status">
          <p className="approvals-bar"><span>1 of 2 approvals</span><span className="muted">{firstApproval.actor.display_name} · {brief(firstApproval.created_at)}</span></p>
          <span className="bar-2" aria-hidden="true"><span className="is-on" /><span /></span>
          <p>
            {firstApproval.actor.display_name} approved this change {when(firstApproval.created_at)}. The change has no known author, so
            a second, different approver must release it.
          </p>
        </Callout>
      ) : null}

      {can.can_approve || can.can_reject ? (
        <section className="card action-card">
          <DecisionConsole
            title="Release payment"
            hint="Approving lifts the hold in Ramp and restores the saved payment dates. Rejecting keeps the hold on."
            caseId={c.id} version={c.version} path={path} action={releaseAction}
            summary={<p className="console-figure"><span className="figure">{bills}</span><span className="muted sub">to {c.vendor.legal_name}</span></p>}
            buttons={[
              ...(can.can_approve ? [{ value: "approve", label: c.unknown_changer ? "Approve and release" : "Release", tone: "primary" as const }] : []),
              ...(can.can_reject ? [{ value: "reject", label: "Reject", tone: "danger" as const }] : []),
            ]}
          >
            <dl className="facts compact">
              <dt>Decision</dt><dd><OutcomeBadge outcome={decision?.outcome ?? null} /></dd>
              <dt>Entered by</dt><dd>{change.changed_by?.display_name ?? "Unknown"}</dd>
              <dt>Approvals</dt><dd>{approvals} of {need}</dd>
            </dl>
          </DecisionConsole>
        </section>
      ) : null}

      {can.can_review ? (
        <section className="card action-card">
          <DecisionConsole
            title="Review case"
            hint="Clearing sends the case to release, where a different approver must still approve it."
            caseId={c.id} version={c.version} path={path} action={reviewAction}
            summary={<p className="muted sub">{bills} · {stateLabel(c.state)}</p>}
            buttons={[{ value: "clear", label: "Clear to release", tone: "primary" }, { value: "block", label: "Block", tone: "danger" }]}
          >
            {lastStepSummary ? <Callout tone="warn"><p>{lastStepSummary}</p></Callout> : null}
          </DecisionConsole>
        </section>
      ) : null}

      {c.state === "blocked" ? (
        <section className="card action-card" aria-labelledby="blocked-h">
          <div className="console-head">
            <h2 id="blocked-h">Case blocked <OutcomeBadge outcome={decision?.outcome ?? null} /></h2>
            <p className="muted sub">{bills}</p>
          </div>
          <div className="console-body">
            {decision?.effect || lastStepSummary ? <Callout tone="bad"><p>{decision?.effect ?? lastStepSummary}</p></Callout>
              : <p className="muted sub">The hold stays on.</p>}
          </div>
        </section>
      ) : null}

      {c.state === "released" ? (
        <section className="card action-card" aria-labelledby="released-h">
          <div className="console-head">
            <h2 id="released-h">Released <span className="badge tone-ok">Hold lifted</span></h2>
            <p className="muted sub">{when(hold?.released_at)}</p>
          </div>
          <div className="console-body">
            {hold?.schedule.length ? (
              <dl className="facts compact">
                {hold.schedule.map((s) => <div key={s.bill_id} className="fact-row"><dt className="mono" title={s.bill_id}>{s.bill_id.slice(0, 8)}</dt><dd>Restored · {date(s.payment_date)}</dd></div>)}
              </dl>
            ) : null}
            {releasedBy ? (
              <div className="approval">
                <p><strong>{releasedBy.actor.display_name}</strong> <span className="muted sub">{when(releasedBy.created_at)}</span></p>
                <blockquote>{releasedBy.reason}</blockquote>
              </div>
            ) : null}
            <p><Link href={`/d/${datasetId}/control/evidence`}>View evidence</Link></p>
          </div>
        </section>
      ) : null}

      {can.reasons.length > 0 && !consoles ? (
        <section className="card action-card" aria-label="Why no action is offered">
          <div className="console-body">
            <Callout icon={<Icon name="lock" size={18} />} title="You can't act on this case">
              <ul className="plain">{can.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </Callout>
            {c.state === "awaiting_release" ? <button type="button" className="primary wide" disabled>Release</button> : null}
          </div>
        </section>
      ) : null}

      {detail.releases.length > 0 ? (
        <section className="card action-card" aria-labelledby="releases-h">
          <div className="console-head"><h2 id="releases-h">Approvals</h2></div>
          <ul className="approvals console-body">
            {detail.releases.map((r) => (
              <li key={`${r.actor.id}-${r.created_at}`}>
                <div><strong>{r.actor.display_name}</strong> <span className="muted">{r.action === "approve" ? "approved" : "rejected"} version {r.case_version}</span></div>
                <div className="muted sub">{when(r.created_at)}</div>
                <blockquote>{r.reason}</blockquote>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
