const stamp = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
const day = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" });

/** Times are shown in UTC, labeled, so lab datasets' simulated times read the same everywhere. */
export function when(iso: string | null | undefined): string {
  return iso ? `${stamp.format(new Date(iso))} UTC` : "—";
}

export function date(iso: string | null | undefined): string {
  return iso ? day.format(new Date(`${iso}T00:00:00Z`)) : "—";
}

export function words(state: string): string {
  return state.replaceAll("_", " ");
}

const DAY_MS = 86_400_000;

/** The dataset's "now": its simulated clock for lab datasets, the real clock otherwise. */
export function datasetNow(simNow: string | null | undefined): Date {
  return simNow ? new Date(simNow) : new Date();
}

/** How far a saved payment date is from today (in UTC days): "due today", "due in 2 days", "3 days overdue". */
export function dueLabel(isoDate: string | null | undefined, now: Date): { text: string; soon: boolean } | null {
  if (!isoDate) return null;
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const days = Math.round((Date.parse(`${isoDate}T00:00:00Z`) - today) / DAY_MS);
  if (days < 0) return { text: `${-days} day${days === -1 ? "" : "s"} overdue`, soon: true };
  if (days === 0) return { text: "due today", soon: true };
  return { text: `due in ${days} day${days === 1 ? "" : "s"}`, soon: days <= 3 };
}

/** A time relative to now, in the largest whole unit: "in 2 days", "5 hours ago". */
export function relative(iso: string | null | undefined, now: Date): string {
  if (!iso) return "";
  const ms = Date.parse(iso) - now.getTime();
  const abs = Math.abs(ms);
  const [n, unit] = abs >= DAY_MS ? [Math.floor(abs / DAY_MS), "day"] : abs >= 3_600_000 ? [Math.floor(abs / 3_600_000), "hour"]
    : [Math.max(1, Math.floor(abs / 60_000)), "minute"];
  const span = `${n} ${unit}${n === 1 ? "" : "s"}`;
  return ms >= 0 ? `in ${span}` : `${span} ago`;
}
