import Link from "next/link";
import type { CaseSummary } from "@/lib/api-types";
import { STATIONS, dayWindow, place, type StationCount } from "@/lib/flow";
import { date, dueLabel, when } from "@/lib/format";
import type { Stage } from "@/lib/progress";
import { Icon } from "./icons";

// The money path as a rail (spec 0032): the same five stations on the queue (how many changes
// sit at each) and on a case (where this one is). Each station says what happens there.

/** The queue's rail: a count per station, sign-off split by what it waits for. */
export function QueueRail({ counts }: { counts: StationCount[] }) {
  return (
    <section className="rail-card" aria-labelledby="rail-h">
      <h2 id="rail-h" className="sr-only">Where every change stands</h2>
      <ol className="rail">
        {counts.map((s) => (
          <li key={s.key} className={`station ${s.count ? "has-count" : "is-empty"}`}>
            <span className="station-node"><Icon name={s.key} /></span>
            <span className="station-label">{s.label}</span>
            <span className="station-meaning">{s.meaning}</span>
            <span className="station-count">{s.count}<span className="sr-only"> {s.count === 1 ? "change" : "changes"}</span></span>
            {s.split ? (
              <span className="station-split">
                {s.split.needs_review ? <span className="badge tone-warn">{s.split.needs_review} needs review</span> : null}
                {s.split.awaiting_release ? <span className="badge tone-ok">{s.split.awaiting_release} awaiting approver</span> : null}
                {s.split.blocked ? <span className="badge tone-bad">{s.split.blocked} blocked</span> : null}
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

const MARK: Record<Stage["status"], string> = { done: "✓", failed: "✕", current: "", skipped: "", todo: "" };

/** A case's rail: this change's status at each station (spec 0031's progress rules). */
export function CaseRail({ steps }: { steps: Stage[] }) {
  return (
    <section className="rail-card" aria-label="Progress">
      <ol className="rail is-case">
        {steps.map((s, i) => (
          <li key={s.key} className={`station is-${s.status} st-${s.tone}`} aria-current={s.status === "current" ? "step" : undefined}>
            <span className="station-node">
              <Icon name={s.key} />
              {MARK[s.status] ? <span className="station-mark" aria-hidden="true">{MARK[s.status]}</span> : null}
            </span>
            <span className="station-label">{s.label}<span className="sr-only">: {s.status === "todo" ? "not reached" : s.status}</span></span>
            <span className="station-meaning">{STATIONS[i].meaning}</span>
            <span className="station-note">{s.note}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

const DAY_MS = 86_400_000;
const tick = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** Held payments by due date: a lane per change, its next saved payment date and its deadline,
 *  with today marked and the overdue side shaded. The table below says the same in words. */
export function PaymentLanes({ cases, now, datasetId }: { cases: CaseSummary[]; now: Date; datasetId: string }) {
  const rows = cases.filter((c) => c.next_payment_date).sort((a, b) => a.next_payment_date!.localeCompare(b.next_payment_date!));
  if (rows.length === 0) return null;
  const w = dayWindow(now, rows.flatMap((c) => [c.next_payment_date!, ...(c.deadline_at ? [c.deadline_at] : [])]));
  const today = place(now.toISOString(), w)!.pct;
  const start = Date.parse(`${w.start}T00:00:00Z`);
  const ticks = Array.from({ length: Math.floor(w.days / 7) + 1 }, (_, k) => new Date(start + k * 7 * DAY_MS).toISOString().slice(0, 10));
  return (
    <figure className="card chart" aria-labelledby="lanes-h">
      <div className="chart-head">
        <h2 id="lanes-h">Held payments by due date</h2>
        <ul className="legend" aria-label="Legend">
          <li><span className="key key-pay" aria-hidden="true" />Next saved payment</li>
          <li><span className="key key-deadline" aria-hidden="true" />Case deadline</li>
          <li><span className="key key-overdue" aria-hidden="true" />Before today</li>
        </ul>
      </div>
      <div className="lanes" style={{ "--today": `${today}%` } as React.CSSProperties}>
        <div className="lane lane-axis" aria-hidden="true">
          <span />
          <span className="track">
            {ticks.map((t) => ({ t, pct: place(t, w)!.pct })).filter(({ pct }) => Math.abs(pct - today) >= 6).map(({ t, pct }) => (
              <span key={t} className={`tick${pct > 92 ? " is-end" : ""}`} style={{ left: `${pct}%` }}>{tick.format(new Date(`${t}T00:00:00Z`))}</span>
            ))}
            <span className="today-label" style={{ left: `${today}%` }}>Today</span>
          </span>
        </div>
        {rows.map((c) => {
          const pay = place(c.next_payment_date, w)!;
          const dl = place(c.deadline_at, w);
          const due = dueLabel(c.next_payment_date, now);
          const words = `${c.vendor.legal_name}: ${c.bills_held} held bill${c.bills_held === 1 ? "" : "s"}, next payment ${date(c.next_payment_date)}${due ? ` (${due.text})` : ""}`;
          return (
            <div key={c.id} className="lane">
              <Link className="lane-name" href={`/d/${datasetId}/cases/${c.id}`}>{c.vendor.legal_name}</Link>
              <span className="track">
                <span className="today-line" aria-hidden="true" />
                {dl ? (
                  <span className={`mark mark-deadline${dl.off ? ` is-off-${dl.off}` : ""}`} style={{ left: `${dl.pct}%` }}
                        title={`${c.vendor.legal_name}: case deadline ${when(c.deadline_at)}`} aria-hidden="true" />
                ) : null}
                <span className={`mark mark-pay${pay.off ? ` is-off-${pay.off}` : ""}${due?.soon ? " is-soon" : ""}`} style={{ left: `${pay.pct}%` }}
                      title={words} role="img" aria-label={words}>
                  {c.bills_held > 1 ? <span className="mark-count">{c.bills_held}</span> : null}
                </span>
              </span>
            </div>
          );
        })}
      </div>
      <figcaption className="muted sub">Times in UTC. Each change&apos;s full row, in words, is in the table below.</figcaption>
    </figure>
  );
}
