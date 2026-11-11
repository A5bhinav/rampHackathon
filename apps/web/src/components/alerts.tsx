import Link from "next/link";
import type { Alert } from "@/lib/api-types";
import { alertChip } from "@/lib/alerts";
import { when } from "@/lib/format";
import { Icon } from "./icons";

export const TITLES: Record<string, string> = {
  hold_not_confirmed: "Hold not confirmed",
  vendor_not_held: "Vendor not held",
  chain_broken: "Evidence chain broken",
  command_stuck: "A step could not finish",
  bad_webhook_signature: "Webhook with a bad signature",
  no_provable_number: "No provable number",
  deadline_reached: "Deadline reached",
  not_verified: "Not verified",
};

/** Critical and warning alerts across the top of every page (design: "Hold not confirmed" banner),
 *  each linked to its case when it has one. */
export function AlertBanners({ alerts, datasetId }: { alerts: Alert[]; datasetId: string }) {
  const shown = alerts.filter((a) => a.severity !== "info").slice(0, 5);
  if (shown.length === 0) return null;
  return (
    <section className="banners" aria-label="Alerts">
      {shown.map((a) => (
        <div key={a.id} className={`banner ${a.severity}`} role={a.severity === "critical" ? "alert" : "status"}>
          <Icon name="alert" size={18} />
          <span className="banner-text"><strong>{TITLES[a.kind] ?? a.kind}</strong> <span>{a.message}</span></span>
          <span className="banner-meta">
            <span>{when(a.raised_at)}</span>
            {a.case_id ? <Link href={`/d/${datasetId}/cases/${a.case_id}`}>Open the case</Link> : null}
          </span>
        </div>
      ))}
    </section>
  );
}

/** The top bar's alert chip (spec 0066): "2 critical", which opens the list. A case page shows its own
 *  alerts as banners instead (AlertBanners with alertsForCase). */
export function AlertChip({ alerts, datasetId }: { alerts: Alert[]; datasetId: string }) {
  const chip = alertChip(alerts);
  if (!chip) return null;
  const shown = alerts.filter((a) => a.severity !== "info")
    .sort((x, y) => (x.severity === y.severity ? y.raised_at.localeCompare(x.raised_at) : x.severity === "critical" ? -1 : 1));
  return (
    <details className="alert-chip">
      <summary className={`badge tone-${chip.tone}`} aria-label={`Alerts: ${chip.label}`}>{chip.label}</summary>
      {chip.tone === "bad" ? <span className="sr-only" role="alert">{`Alerts: ${chip.label}`}</span> : null}
      <ul className="alert-pop">
        {shown.map((a) => (
          <li key={a.id}>
            <span className={`badge tone-${a.severity === "critical" ? "bad" : "warn"}`}>{TITLES[a.kind] ?? a.kind}</span>
            <p>{a.message}</p>
            <p className="muted sub">{when(a.raised_at)}{a.case_id ? <> · <Link href={`/d/${datasetId}/cases/${a.case_id}`}>Open the case</Link></> : null}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}
