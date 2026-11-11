// The two apps (spec 0063): AP workspace for clerks and approvers, Control for controllers and
// auditors. Each sidebar lists exactly its board's items. Which app a person is offered is a display
// choice; the core and the database decide what anyone can read or do.
import type { IconName } from "../components/icons";

export type AppKey = "ap" | "control";
export type AppNavItem = { label: string; href: string; icon: IconName; group?: "Settings"; exact?: boolean; not?: string[] };

type Item = { label: string; path: string; icon: IconName; group?: "Settings"; exact?: boolean; not?: string[] };

const AP: Item[] = [
  { label: "Inbox", path: "cases", icon: "inbox", not: ["cases/all"] },
  { label: "Cases", path: "cases/all", icon: "folder" },
  { label: "Vendors", path: "vendors", icon: "building" },
];

const CONTROL: Item[] = [
  { label: "Overview", path: "control", icon: "grid", exact: true },
  { label: "Trust Ledger", path: "control/trust-ledger", icon: "shield" },
  { label: "Evidence", path: "control/evidence", icon: "link" },
  { label: "Assurance", path: "control/assurance", icon: "check-circle" },
  { label: "Rules & policy", path: "control/rules", icon: "ledger", group: "Settings" },
  { label: "People", path: "control/people", icon: "person", group: "Settings" },
  { label: "Integrations", path: "control/integrations", icon: "chain", group: "Settings" },
];

export function navFor(app: AppKey, datasetId: string): AppNavItem[] {
  const base = `/d/${datasetId}`;
  return (app === "ap" ? AP : CONTROL).map(({ path, not, ...rest }) => ({
    ...rest, href: `${base}/${path}`, ...(not ? { not: not.map((n) => `${base}/${n}`) } : {}),
  }));
}

/** Whether a nav item is the page being shown: its own path, or below it unless excluded. */
export function isCurrent(item: Pick<AppNavItem, "href" | "exact" | "not">, path: string): boolean {
  if (item.not?.some((n) => path === n || path.startsWith(`${n}/`))) return false;
  return path === item.href || (!item.exact && path.startsWith(`${item.href}/`));
}

export function switchTargets(datasetId: string): Record<AppKey, string> {
  return { ap: `/d/${datasetId}/cases`, control: `/d/${datasetId}/control` };
}

export function appOf(path: string): AppKey {
  return /^\/d\/[^/]+\/control(\/|$)/.test(path) ? "control" : "ap";
}

/** Control is offered to everyone on demo data, and to approvers, auditors and admins elsewhere (spec 0063 Q1, spec 0095). */
export function canSeeControl(roles: readonly string[], kind: string): boolean {
  return kind === "demo" || roles.includes("approver") || roles.includes("auditor") || roles.includes("admin");
}

const APP_LABEL: Record<AppKey, string> = { ap: "AP workspace", control: "Control" };

/** Every page's place for the top bar: the app, then the page (its sidebar label), so the bar is never
 *  empty. Case and Ramp vendor pages build their own deeper trail; anything unknown shows the app only. */
export function pageCrumb(app: AppKey, datasetId: string, path: string): { app: { label: string; href: string }; page: string | null } {
  const base = `/d/${datasetId}`;
  const extra: AppNavItem[] = [
    { label: "Ramp (mock)", href: `${base}/ramp`, icon: "card" },
    { label: "Demo controls", href: `${base}/control/demo`, icon: "lock" },
  ];
  const item = [...navFor(app, datasetId), ...extra].find((i) => isCurrent(i, path));
  return { app: { label: APP_LABEL[app], href: switchTargets(datasetId)[app] }, page: item?.label ?? null };
}
