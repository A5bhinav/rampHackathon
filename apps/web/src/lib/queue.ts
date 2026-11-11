// Who a case is waiting on (spec 0066, PR 3). One rule, shared by the inbox and the case page's lock
// card: a person can act on a case when the core offers them a console (approve, reject or review).
// The core computes those permissions (R5 among them: never the person who entered the change), and
// Postgres refuses whatever the page shows; this only labels links. Unknown permissions fail closed.
import type { CaseState } from "./api-types";

type Permissions = { can_approve: boolean; can_reject: boolean; can_review: boolean };

export const NEEDS_A_PERSON: readonly CaseState[] = ["needs_review", "awaiting_release", "blocked"];

export function canAct(p: Permissions | null | undefined): boolean {
  return Boolean(p && (p.can_approve || p.can_reject || p.can_review));
}

/** The waiting cases the signed-in person can act on, in the order given. */
export function needsYou<T extends { id: string; state: CaseState }>(cases: readonly T[], perms: ReadonlyMap<string, Permissions | null>): T[] {
  return cases.filter((c) => NEEDS_A_PERSON.includes(c.state) && canAct(perms.get(c.id)));
}

/** A row's link label: release or review only for someone who can act on it. */
export function rowAction(state: CaseState | string, mine: boolean): "Review and release" | "Review" | "Open" {
  if (mine && state === "awaiting_release") return "Review and release";
  if (mine && state === "needs_review") return "Review";
  return "Open";
}
