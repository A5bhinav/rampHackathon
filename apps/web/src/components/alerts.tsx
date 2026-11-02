import type { Alert } from "@/lib/api-types";

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

/** Critical and warning alerts across the top of every page (design: "Hold not confirmed" banner). */
export function AlertBanners({ alerts }: { alerts: Alert[] }) {
  const shown = alerts.filter((a) => a.severity !== "info").slice(0, 5);
  if (shown.length === 0) return null;
  return (
    <section aria-label="Alerts">
      {shown.map((a) => (
        <div key={a.id} className={`banner ${a.severity}`} role={a.severity === "critical" ? "alert" : "status"}>
          {TITLES[a.kind] ?? a.kind}: <span style={{ fontWeight: 400 }}>{a.message}</span>
        </div>
      ))}
    </section>
  );
}
