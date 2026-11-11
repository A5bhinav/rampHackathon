import Link from "next/link";
import { OutcomeBadge, StateBadge } from "./badges";
import { MiniRail } from "./flow";
import { stationsFromEvidence, type StationView } from "@/lib/stations";
import type { CaseSummary } from "@/lib/api-types";
import { core } from "@/lib/core";
import { brief, date, dueLabel, relative, when } from "@/lib/format";
import { NEEDS_A_PERSON, readPermissions, rowAction } from "@/lib/queue";
import type { CaseDetail } from "@/lib/api-types";
type Permissions = CaseDetail["permissions"];

// The case table shared by the Inbox and the Cases list (specs 0041, 0063): each row's vendor, state
// with its mini rail, decision, held bills, next payment, deadline and the one action it waits for.

export { NEEDS_A_PERSON };

export type CasePermissions = ReadonlyMap<string, Permissions | null>;

/** The core's own permissions for each waiting case: what the case page's lock card reads (spec 0066).
 *  Capped and batched; a case whose permissions can't be read counts as nothing to act on. */
export function actionable(cases: readonly CaseSummary[]): Promise<CasePermissions> {
  return readPermissions(cases, (id) => core.case(id).then((d) => d.permissions));
}

export function bySoonestPayment(a: CaseSummary, b: CaseSummary): number {
  const ka = a.next_payment_date ?? "9999-12-31";
  const kb = b.next_payment_date ?? "9999-12-31";
  return ka === kb ? a.opened_at.localeCompare(b.opened_at) : ka.localeCompare(kb);
}

// A stripe on the vendor cell for what needs eyes first: an unconfirmed hold, a payment due soon, a block.
function stripe(c: CaseSummary, soon: boolean): string {
  if (c.hold_status === "failed" || soon || c.state === "blocked") return "is-bad";
  if (c.state === "needs_review") return "is-warn";
  if (c.state === "awaiting_release") return "is-ok";
  return "";
}

// These only label links; the case page and the database decide who may act.
function action(c: CaseSummary, href: string, perms: CasePermissions) {
  const label = rowAction(c.state, perms.get(c.id));
  if (label === "Review and release") return <Link className="button-link primary" href={href}>{label}</Link>;
  if (label === "Review") return <Link className="button-link" href={href}>{label}</Link>;
  return <Link className="open-link" href={href}>Open</Link>;
}

function Held({ c }: { c: CaseSummary }) {
  if (c.hold_status === "failed") return <span className="badge tone-bad">hold not confirmed</span>;
  return <><span className="held-n">{c.bills_held}</span> <span className="muted sub">{c.bills_held === 1 ? "bill" : "bills"}</span></>;
}

export function QueueTable({ id, title, cases, datasetId, now, rails, perms }: {
  id: string; title: string; cases: CaseSummary[]; datasetId: string; now: Date; rails?: Map<string, StationView[]>;
  perms: CasePermissions;  // the core's permissions for the waiting cases (actionable)
}) {
  return (
    <section id={id} className="card queue-group" aria-label={title}>
      <div className="card-head">
        <h2>{title} <span className="count">{cases.length}</span></h2>
      </div>
      <div className="table-wrap queue-table">
        <table>
          <thead>
            <tr><th className="w-vendor">Vendor</th><th>Status</th><th>Decision</th><th className="num">Held</th><th>Next payment</th><th>Deadline</th><th className="num"><span className="sr-only">Action</span></th></tr>
          </thead>
          <tbody>
            {cases.map((c) => {
              const due = dueLabel(c.next_payment_date, now);
              const href = `/d/${datasetId}/cases/${c.id}`;
              return (
                <tr key={c.id} className={stripe(c, Boolean(due?.soon))}>
                  <td>
                    <Link className="vendor-link" href={href}>{c.vendor.legal_name}</Link>
                    <div className="mono cell-meta" title={when(c.opened_at)}>v{c.version} · opened {brief(c.opened_at)}</div>
                    {c.unknown_changer ? <div className="cell-note"><span className="badge tone-warn">two approvers</span></div> : null}
                  </td>
                  <td><StateBadge state={c.state} /><MiniRail views={rails?.get(c.id) ?? stationsFromEvidence([], c)} /></td>
                  <td><OutcomeBadge outcome={c.outcome} /></td>
                  <td className="num nowrap"><Held c={c} /></td>
                  <td className="nowrap">
                    {date(c.next_payment_date)}
                    {due ? <div className="cell-note"><span className={`badge ${due.soon ? "tone-bad" : "tone-muted"}`}>{due.text}</span></div> : null}
                  </td>
                  <td className="nowrap">
                    <span title={when(c.deadline_at)}>{brief(c.deadline_at) || "—"}</span>
                    {c.deadline_at && c.state !== "releasing" ? (  // as on the case page: no countdown once a release is under way
                      <div className="muted sub">{Date.parse(c.deadline_at) < now.getTime() ? "passed" : relative(c.deadline_at, now)}</div>
                    ) : null}
                  </td>
                  <td className="num">{action(c, href, perms)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ul className="queue-cards">
        {cases.map((c) => {
          const due = dueLabel(c.next_payment_date, now);
          const href = `/d/${datasetId}/cases/${c.id}`;
          return (
            <li key={c.id} className={stripe(c, Boolean(due?.soon))}>
              <div className="qc-line"><Link className="vendor-link" href={href}>{c.vendor.legal_name}</Link><span className="nowrap"><Held c={c} /></span></div>
              <div className="qc-line">
                <StateBadge state={c.state} />
                <span className={due?.soon ? "tone-text-bad sub" : "muted sub"}>{due ? due.text : c.state === "blocked" ? "Hold stays on" : ""}</span>
              </div>
              <div className="qc-action">{action(c, href, perms)}</div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
