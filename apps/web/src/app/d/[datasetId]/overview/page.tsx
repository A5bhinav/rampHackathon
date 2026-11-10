import Link from "next/link";
import { notFound } from "next/navigation";
import { TITLES } from "@/components/alerts";
import { ChainBadge } from "@/components/badges";
import { CardHead, PageHead } from "@/components/page";
import type { CaseState } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { alertsBySeverity, countByState, heldByVendor, holdsNotConfirmed } from "@/lib/overview";
import { MAIN, MAIN_EVENTS, PERSON_EVENTS, stateLabel } from "@/lib/states";

// The overview (spec 0041): the dataset's health from what the core reports (the chain, the
// scheduled check, alerts, holds not confirmed), the live state machine with a count per state,
// and held bills by vendor. Nothing here is reported that the core doesn't send.
const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 } as const;

export default async function OverviewPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const [cases, alerts, log, datasets] = await Promise.all([
    core.allCases(datasetId), core.alerts(datasetId), core.evidence(datasetId).catch((e) => {
      if (e instanceof CoreError && e.status === 404) notFound();
      throw e;
    }), core.datasets(),
  ]);
  const dataset = datasets.find((d) => d.id === datasetId);
  const byState = countByState(cases);
  const notConfirmed = holdsNotConfirmed(cases);
  const sev = alertsBySeverity(alerts);
  const held = heldByVendor(cases);
  const most = Math.max(1, ...held.map((h) => h.bills));
  const sorted = [...alerts].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || b.raised_at.localeCompare(a.raised_at));

  return (
    <main>
      <PageHead eyebrow="Control" title="Overview" lead={`${dataset?.label ?? "This dataset"}. Times in UTC.`} />
      <dl className="stats" aria-label="Health">
        <div className="stat"><dt>Evidence chain</dt><dd><Link className="badge-link" href={`/d/${datasetId}/evidence`}><ChainBadge chain={log.chain} /></Link></dd>
          <dd className="stat-sub">{log.chain.checked} rows recomputed</dd></div>
        <div className="stat"><dt>Last chain check</dt><dd className="stat-small">{log.last_check?.finished_at ? when(log.last_check.finished_at) : "not run yet"}</dd></div>
        <div className={`stat${notConfirmed ? " is-bad" : ""}`}><dt>Holds not confirmed</dt><dd>{notConfirmed}</dd></div>
        <div className={`stat${sev.critical ? " is-bad" : sev.warning ? " is-warn" : ""}`}><dt>Open alerts</dt><dd>{sev.critical + sev.warning + sev.info}</dd>
          <dd className="stat-sub">{sev.critical} critical · {sev.warning} warning · {sev.info} info</dd></div>
      </dl>

      <section className="card machine-card" aria-labelledby="machine-h">
        <CardHead marker="State machine" title="Cases by state" id="machine-h" meta={<span className="muted sub">{cases.length} cases</span>} />
        <div className="card-body">
          <ol className="machine" aria-label="The happy path, left to right">
            {MAIN.map((s, i) => (
              <li key={s} className="machine-step">
                <Node state={s} n={byState[s]} />
                {i < MAIN_EVENTS.length ? (
                  <span className={`edge${PERSON_EVENTS.includes(MAIN_EVENTS[i]) ? " is-person" : ""}`}>
                    <span className="mono">{MAIN_EVENTS[i]}</span><span aria-hidden="true">›</span>
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
          <ul className="machine-branch" aria-label="Where a case waits off the happy path">
            <li className="b-retry"><Node state="retry_wait" n={byState.retry_wait} /></li>
            <li className="b-review"><Node state="needs_review" n={byState.needs_review} /></li>
            <li className="b-blocked"><Node state="blocked" n={byState.blocked} /></li>
          </ul>
          <p className="machine-legend">
            <span className="mono person-key">release_approved · release_co_approved · release_rejected · review_cleared · review_blocked</span>
            <span>Only a person can make these moves</span>
          </p>
          <p className="muted sub">Every failure path ends where the hold stays on. Released is the only finished state.</p>
        </div>
      </section>

      <div className="two-up">
        <section className="card" aria-labelledby="held-h">
          <CardHead marker="Held" title="Held bills by vendor" id="held-h" />
          <div className="card-body">
            {held.length ? (
              <ul className="hbars">
                {held.map((h) => (
                  <li key={h.case_id}>
                    <Link href={`/d/${datasetId}/cases/${h.case_id}`}>{h.vendor}</Link>
                    <span className="hbar" aria-hidden="true"><span style={{ width: `${(h.bills / most) * 100}%` }} /></span>
                    <span className="hbar-n">{h.bills} <span className="muted sub">{h.bills === 1 ? "bill" : "bills"}</span></span>
                  </li>
                ))}
              </ul>
            ) : <p className="muted sub">No bills are on a confirmed hold.</p>}
          </div>
        </section>
        <section className="card" aria-labelledby="alerts-h">
          <CardHead marker="Alerts" title="Open alerts" id="alerts-h" meta={<span className="count">{alerts.length}</span>} />
          {sorted.length ? (
            <ul className="alert-list">
              {sorted.map((a) => (
                <li key={a.id}>
                  <span className={`badge tone-${a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "muted"}`}>{a.severity}</span>
                  <div>
                    <p><strong>{TITLES[a.kind] ?? a.kind}</strong> {a.message}</p>
                    <p className="muted sub">{when(a.raised_at)}{a.case_id ? <> · <Link href={`/d/${datasetId}/cases/${a.case_id}`}>Open the case</Link></> : null}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : <p className="card-body muted sub">No open alerts.</p>}
        </section>
      </div>
    </main>
  );
}

function Node({ state, n }: { state: CaseState; n: number }) {
  return (
    <span className={`node node-${state}${n ? " has-n" : ""}`}>
      <span className="node-label">{stateLabel(state)}</span>
      <span className="node-n">{n}</span>
    </span>
  );
}
