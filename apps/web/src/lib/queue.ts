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

/** A row's link label: release only for someone who can approve; review for anyone else who can act. */
export function rowAction(state: CaseState | string, p: Permissions | null | undefined): "Review and release" | "Review" | "Open" {
  if (state === "awaiting_release" && p?.can_approve) return "Review and release";
  if ((state === "awaiting_release" || state === "needs_review") && canAct(p)) return "Review";
  return "Open";
}

/** The core's permissions for the waiting cases: the first `limit` of them, `batch` reads at a time
 *  (each read recomputes the case's chain). A read that fails is null, and a case past the cap isn't
 *  read: both count as nothing to act on (fail closed). */
export async function readPermissions<P extends Permissions>(cases: readonly { id: string; state: CaseState }[],
  read: (id: string) => Promise<P>, { limit = 50, batch = 6 } = {}): Promise<Map<string, P | null>> {
  const waiting = cases.filter((c) => NEEDS_A_PERSON.includes(c.state)).slice(0, limit);
  const out = new Map<string, P | null>();
  for (let i = 0; i < waiting.length; i += batch) {
    const chunk = waiting.slice(i, i + batch);
    const got = await Promise.all(chunk.map((c) => read(c.id).catch(() => null)));
    chunk.forEach((c, j) => out.set(c.id, got[j]));
  }
  return out;
}
