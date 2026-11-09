import Link from "next/link";
import { notFound } from "next/navigation";
import { ChainBadge, StateBadge } from "@/components/badges";
import { DecisionConsole } from "@/components/console";
import { DecisionDetail } from "@/components/decision";
import { PageHead } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { brief, datasetNow, date, dueLabel, relative, when, words } from "@/lib/format";
import { progress } from "@/lib/progress";
import { releaseAction, reviewAction } from "./actions";

// Case detail and the release console: the timeline from the evidence log, the chain badge,
// the decision with the rules that fired, and (only for someone the rules allow) release or
// review. The database refuses the changer whatever this page shows.
const NOTICES: Record<string, string> = {
  applied: "Done.",
  already_applied: "That was already recorded.",
  awaiting_second_approver: "Your approval is recorded. A second approver must release this change.",
};

const EDGE: Record<string, string> = { verified: "edge-ok", not_verified: "edge-bad", needs_review: "edge-warn" };

export default async function CaseDetailPage({ params, searchParams }: {
  params: Promise<{ datasetId: string; caseId: string }>;
  searchParams: Promise<{ done?: string; to?: string }>;
}) {
  const [{ datasetId, caseId }, { done, to }] = await Promise.all([params, searchParams]);
  const [detail, datasets] = await Promise.all([
    core.case(caseId).catch((e) => {
      if (e instanceof CoreError && e.status === 404) notFound();
      throw e;
    }),
    core.datasets(),
  ]);
  const { case: c, change, hold, decision, permissions: can } = detail;
  const path = `/d/${datasetId}/cases/${caseId}`;
  const now = datasetNow(datasets.find((d) => d.id === datasetId)?.sim_now);
  const deadlinePassed = c.deadline_at ? Date.parse(c.deadline_at) < now.getTime() : false;
  // ADR-10: with no known changer, the first approval for this version waits for a second approver.
  const firstApproval = c.unknown_changer && c.state === "awaiting_release"
    ? detail.releases.find((r) => r.action === "approve" && r.case_version === c.version) : undefined;
  const lastStep = detail.timeline.at(-1);  // its summary is the core's template, so the reason is never guessed here

  const visited = detail.timeline.filter((e) => e.version === c.version).map((e) => e.to_state);
  const steps = progress({ state: c.state, visited, holdStatus: hold?.status ?? null, hasDecision: Boolean(decision) }).map((s) => {
    if (s.status !== "done") return s;
    if (s.key === "hold") return { ...s, note: brief(hold?.confirmed_at) };
    if (s.key === "call") return { ...s, note: "Answered" };
    if (s.key === "decision" && decision) return { ...s, note: words(decision.outcome) };
    if (s.key === "release") return { ...s, note: brief(hold?.released_at) || "Released" };
    return s;
  });

  return (
    <main>
      {done && NOTICES[done] ? (
        <p className="result ok" role="status">{NOTICES[done]}{to ? ` The case moved to ${words(to)}.` : ""}</p>
      ) : null}
      <PageHead
        back={{ href: `/d/${datasetId}/cases`, label: "Case queue" }}
        title={c.vendor.legal_name}
        aside={<Link className="button-link" href={`${path}/lineage`}>Number lineage</Link>}
      >
        <div className="tags">
          <StateBadge state={c.state} />
          <ChainBadge chain={detail.chain} />
          {c.unknown_changer ? <span className="badge tone-warn">Unknown changer: two approvers</span> : null}
          {c.deadline_at && !["released", "releasing"].includes(c.state) ? (
            <span className={`badge ${deadlinePassed ? "tone-bad" : "tone-muted"}`} title={when(c.deadline_at)}>
              Deadline {deadlinePassed ? "passed" : relative(c.deadline_at, now)}
            </span>
          ) : null}
          <span className="muted sub">Version {c.version}</span>
        </div>
      </PageHead>

      <ol className="progress" aria-label="Progress">
        {steps.map((s, i) => (
          <li key={s.key} className={`step is-${s.status} step-${s.tone}`} aria-current={s.status === "current" ? "step" : undefined}>
            <span className="step-dot" aria-hidden="true">{s.status === "done" ? "✓" : s.status === "current" ? "" : i + 1}</span>
            <span className="step-text">
              <span className="step-label">{s.label}</span>
              <span className="step-note">{s.note}</span>
            </span>
          </li>
        ))}
      </ol>

      <div className="case-layout">
        <div className="case-main">
          {firstApproval ? (
            <p className="result ok" role="status">
              {firstApproval.actor.display_name} approved this change {when(firstApproval.created_at)}. The change has no known author, so
              a second, different approver must release it.
            </p>
          ) : null}

          <section className={`card lead-card ${decision ? EDGE[decision.outcome] ?? "" : ""}`} aria-labelledby="decision-h">
            <h2 id="decision-h">The decision</h2>
            {decision ? <DecisionDetail decision={decision} /> : (
              <p className="muted">
                {["needs_review", "blocked"].includes(c.state) && lastStep
                  ? <>Code made no decision for this version. The last step: {lastStep.summary}</>
                  : "No decision yet for this version."}
              </p>
            )}
          </section>

          {can.can_approve || can.can_reject ? (
            <section className="card console-card">
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
            <section className="card console-card">
              <DecisionConsole
                title="Review"
                hint="Clearing sends the case to release, where a different approver must still approve it."
                caseId={c.id} version={c.version} path={path} action={reviewAction}
                buttons={[{ value: "clear", label: "Clear for release", tone: "primary" }, { value: "block", label: "Block", tone: "danger" }]}
              />
            </section>
          ) : null}
          {can.reasons.length > 0 ? (
            <section className="note" aria-label="Why no action is offered">
              <ul className="plain">{can.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </section>
          ) : null}

          <section className="section" aria-labelledby="timeline-h">
            <h2 id="timeline-h">Timeline <span className="h-note">from the evidence log</span></h2>
            <ol className="timeline">
              {detail.timeline.map((e) => (
                <li key={e.seq}>
                  <span className="seq">#{e.seq}</span>
                  <div>
                    <div>{e.summary}</div>
                    <div className="meta">
                      <span>{when(e.occurred_at)}</span>
                      <span>{e.actor.display_name}</span>
                      <span>{e.from_state ? `${words(e.from_state)} → ` : ""}{words(e.to_state)}</span>
                      <span className="mono" title={e.hash}>{e.hash.slice(0, 12)}</span>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>

        <aside className="case-side" aria-label="The change and the hold">
          <section className="panel" aria-labelledby="change-h">
            <h2 id="change-h">The change</h2>
            <div className="accounts">
              <div className="account">
                <span className="account-label">Old account</span>
                <span className="mono">{change.old_account ? `${change.old_account.routing_number} ···${change.old_account.last4}` : "none on file"}</span>
              </div>
              <div className="account is-new">
                <span className="account-label">New account</span>
                <span className="mono">{change.new_account.routing_number} ···{change.new_account.last4}</span>
              </div>
            </div>
            <dl className="facts">
              <dt>Detected</dt><dd>{when(change.detected_at)}<div className="muted sub">via {change.detected_via}</div></dd>
              <dt>Entered by</dt><dd>{change.changed_by?.display_name ?? "Unknown (vendor-supplied or synced)"}</dd>
            </dl>
          </section>

          <section className="panel" aria-labelledby="hold-h">
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
                    <h3 className="panel-sub">Saved payment dates, restored on release</h3>
                    <ul className="schedule">
                      {hold.schedule.map((s) => {
                        const due = hold.released_at ? null : dueLabel(s.payment_date, now);
                        return (
                          <li key={s.bill_id}>
                            <span className="mono muted" title={s.bill_id}>{s.bill_id.slice(0, 8)}</span>
                            <span>{date(s.payment_date)}</span>
                            {due ? <span className={`badge ${due.soon ? "tone-bad" : "tone-muted"}`}>{due.text}</span> : <span />}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : null}
                {hold.last_error ? <p className="result error">{hold.last_error}</p> : null}
              </>
            ) : <p className="muted">Being placed.</p>}
          </section>

          {detail.releases.length > 0 ? (
            <section className="panel" aria-labelledby="releases-h">
              <h2 id="releases-h">Approvals</h2>
              <ul className="approvals">
                {detail.releases.map((r) => (
                  <li key={`${r.actor.id}-${r.created_at}`}>
                    <div><strong>{r.actor.display_name}</strong> {r.action === "approve" ? "approved" : "rejected"} version {r.case_version}</div>
                    <div className="muted sub">{when(r.created_at)}</div>
                    <blockquote>{r.reason}</blockquote>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </aside>
      </div>
    </main>
  );
}
