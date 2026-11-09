import Link from "next/link";
import type { Alert } from "@/lib/api-types";
import { when } from "@/lib/format";

const TITLES: Record<string, string> = {
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
