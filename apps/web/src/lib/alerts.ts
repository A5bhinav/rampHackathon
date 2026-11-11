// The alerts' placement (spec 0066, PR 1): one compact chip in the top bar for every open alert that
// isn't info, and a full banner only on the case an alert belongs to. Display only: the alerts and
// their messages are the core's.
type Severity = "info" | "warning" | "critical";
type AlertLike = { id: number; severity: Severity; case_id: string | null };

export function alertChip(alerts: readonly AlertLike[]): { label: string; tone: "bad" | "warn"; count: number } | null {
  const critical = alerts.filter((x) => x.severity === "critical").length;
  const warning = alerts.filter((x) => x.severity === "warning").length;
  if (!critical && !warning) return null;
  const parts = [critical ? `${critical} critical` : "", warning ? `${warning} warning` : ""].filter(Boolean);
  return { label: parts.join(" · "), tone: critical ? "bad" : "warn", count: critical + warning };
}

export function alertsForCase<T extends AlertLike>(alerts: readonly T[], caseId: string): T[] {
  return alerts.filter((x) => x.case_id === caseId && x.severity !== "info")
    .sort((x, y) => (x.severity === y.severity ? 0 : x.severity === "critical" ? -1 : 1));
}
