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
