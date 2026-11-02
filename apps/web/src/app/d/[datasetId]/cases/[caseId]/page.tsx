import Link from "next/link";
import { notFound } from "next/navigation";
import { ChainBadge, OutcomeBadge, StateBadge } from "@/components/badges";
import { DecisionConsole } from "@/components/console";
import { CoreError, core } from "@/lib/core";
import { date, when, words } from "@/lib/format";
import { releaseAction, reviewAction } from "./actions";

// Case detail and the release console: the timeline from the evidence log, the chain badge,
// the decision with the rules that fired, and (only for someone the rules allow) release or
// review. The database refuses the changer whatever this page shows.
const NOTICES: Record<string, string> = {
  applied: "Done.",
  already_applied: "That was already recorded.",
  awaiting_second_approver: "Your approval is recorded. A second approver must release this change.",
};

export default async function CaseDetailPage({ params, searchParams }: {
  params: Promise<{ datasetId: string; caseId: string }>;
  searchParams: Promise<{ done?: string; to?: string }>;
}) {
  const [{ datasetId, caseId }, { done, to }] = await Promise.all([params, searchParams]);
  const detail = await core.case(caseId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });
  const { case: c, change, hold, decision, permissions: can } = detail;
  const path = `/d/${datasetId}/cases/${caseId}`;

  return (
    <main>
      <p><Link href={`/d/${datasetId}/cases`}>← Case queue</Link></p>
      {done && NOTICES[done] ? (
        <p className="result ok" role="status">{NOTICES[done]}{to ? ` The case moved to ${words(to)}.` : ""}</p>
      ) : null}
      <h1>{c.vendor.legal_name}</h1>
      <p style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <StateBadge state={c.state} />
        <OutcomeBadge outcome={c.outcome} />
        <ChainBadge chain={detail.chain} />
        <span className="muted">Version {c.version}</span>
        {c.unknown_changer ? <span className="badge tone-warn">Unknown changer: two approvers</span> : null}
      </p>

      <div className="grid">
        <section className="card" aria-labelledby="change-h">
          <h2 id="change-h">The change</h2>
          <dl className="facts">
            <dt>Old account</dt><dd className="mono">{change.old_account ? `${change.old_account.routing_number} ···${change.old_account.last4}` : "none on file"}</dd>
            <dt>New account</dt><dd className="mono">{change.new_account.routing_number} ···{change.new_account.last4}</dd>
            <dt>Detected</dt><dd>{when(change.detected_at)} via {change.detected_via}</dd>
            <dt>Entered by</dt><dd>{change.changed_by?.display_name ?? "Unknown (vendor-supplied or synced)"}</dd>
          </dl>
        </section>

        <section className="card" aria-labelledby="hold-h">
          <h2 id="hold-h">The hold</h2>
          {hold ? (
            <>
              <dl className="facts">
                <dt>Status</dt><dd>{hold.status === "failed" ? <span className="badge tone-bad">not confirmed</span> : words(hold.status)}</dd>
                <dt>Confirmed</dt><dd>{when(hold.confirmed_at)}</dd>
                <dt>Released</dt><dd>{when(hold.released_at)}</dd>
              </dl>
              {hold.schedule.length > 0 ? (
                <>
                  <p className="muted" style={{ marginBottom: 4 }}>Saved payment dates, restored on release:</p>
                  <ul className="plain">{hold.schedule.map((s) => <li key={s.bill_id}><span className="mono">{s.bill_id.slice(0, 8)}</span> · {date(s.payment_date)}</li>)}</ul>
                </>
              ) : null}
              {hold.last_error ? <p className="result error">{hold.last_error}</p> : null}
            </>
          ) : <p className="muted">Being placed.</p>}
        </section>

        <section className="card" aria-labelledby="decision-h">
          <h2 id="decision-h">The decision</h2>
          {decision ? (
            <>
              <p><OutcomeBadge outcome={decision.outcome} /> <span className="muted">{when(decision.decided_at)} · {decision.engine_version}</span></p>
              <ul className="plain">{decision.explanation.map((s) => <li key={s}>{s}</li>)}</ul>
            </>
          ) : <p className="muted">No decision yet for this version.</p>}
        </section>
      </div>

      {can.can_approve || can.can_reject ? (
        <section className="card">
          <DecisionConsole
            title="Release"
            hint="Approving lifts the hold in Ramp and restores the saved payment dates. Rejecting keeps the hold on."
            caseId={c.id} version={c.version} path={path} action={releaseAction}
            buttons={[
              ...(can.can_approve ? [{ value: "approve", label: "Approve release", tone: "primary" as const }] : []),
              ...(can.can_reject ? [{ value: "reject", label: "Reject", tone: "danger" as const }] : []),
            ]}
          />
        </section>
      ) : null}
      {can.can_review ? (
        <section className="card">
          <DecisionConsole
            title="Review"
            hint="Clearing sends the case to release, where a different approver must still approve it."
            caseId={c.id} version={c.version} path={path} action={reviewAction}
            buttons={[{ value: "clear", label: "Clear for release", tone: "primary" }, { value: "block", label: "Block", tone: "danger" }]}
          />
        </section>
      ) : null}
      {can.reasons.length > 0 ? (
        <section className="card" aria-label="Why no action is offered">
          <ul className="plain">{can.reasons.map((r) => <li key={r} className="muted">{r}</li>)}</ul>
        </section>
      ) : null}

      <section className="card" aria-labelledby="timeline-h">
        <h2 id="timeline-h">Timeline, from the evidence log</h2>
        <ol className="timeline">
          {detail.timeline.map((e) => (
            <li key={e.seq}>
              <span className="seq">#{e.seq}</span>
              <div>
                <div>{e.summary}</div>
                <div className="muted" style={{ fontSize: "0.85rem" }}>
                  {when(e.occurred_at)} · {e.actor.display_name} · {e.from_state ? `${words(e.from_state)} → ` : ""}{words(e.to_state)} ·{" "}
                  <span className="mono" title={e.hash}>{e.hash.slice(0, 12)}</span>
                </div>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {detail.releases.length > 0 ? (
        <section className="card" aria-labelledby="releases-h">
          <h2 id="releases-h">Approvals</h2>
          <ul className="plain">
            {detail.releases.map((r) => (
              <li key={`${r.actor.id}-${r.created_at}`}>{r.actor.display_name} {r.action === "approve" ? "approved" : "rejected"} version {r.case_version}, {when(r.created_at)}: “{r.reason}”</li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}
