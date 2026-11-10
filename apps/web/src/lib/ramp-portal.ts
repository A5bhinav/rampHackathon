// The demo's Ramp (mock) portal (spec 0077). Display and form hints only: the core checks the
// numbers, decides who may send a change, and never stores the full account number.

/** Nine digits whose ABA checksum (weights 3, 7, 1) is a multiple of ten. */
export function routingValid(routing: string): boolean {
  if (!/^[0-9]{9}$/.test(routing)) return false;
  const w = [3, 7, 1, 3, 7, 1, 3, 7, 1];
  return [...routing].reduce((n, c, i) => n + w[i] * Number(c), 0) % 10 === 0;
}

export function accountValid(account: string): boolean {
  return /^[0-9]{4,17}$/.test(account);
}

/** Routing number and last four, the only way an account is ever shown (ADR-8). */
export function formatAccount(a: { routing_number: string; last4: string } | null): string {
  return a ? `${a.routing_number} ····${a.last4}` : "No account on file";
}

export type CaseMark = { id: string; version: number };

/** The vendor's newest case, any state: what a send is compared against. */
export function latestCase<T extends { id: string; version: number; opened_at: string; vendor: { id: string } }>(cases: readonly T[], vendorId: string): T | null {
  return cases.filter((c) => c.vendor.id === vendorId).sort((a, b) => b.opened_at.localeCompare(a.opened_at))[0] ?? null;
}

/** "id.version" for the URL, or "none". */
export function markOf(c: CaseMark | null): string {
  return c ? `${c.id}.${c.version}` : "none";
}

export function parseMark(raw: string | undefined): CaseMark | null | undefined {
  if (raw === "none") return null;
  const m = /^([0-9a-f-]{36})\.(\d+)$/.exec(raw ?? "");
  return m ? { id: m[1], version: Number(m[2]) } : undefined;  // undefined: not a send this page made
}

/** Picked up: a new case for the vendor, or the same case at a later version (a new change on it). */
export function pickedUp(latest: CaseMark | null, base: CaseMark | null): boolean {
  if (!latest) return false;
  return !base || latest.id !== base.id || latest.version > base.version;
}

const GIVE_UP_MS = 5 * 60_000;

/** What the vendor page shows after a send: nothing sent, waiting for Dialback, the case it opened,
 *  or, five minutes on with no case, that none has opened yet (and it stops refreshing). */
export function watchFor(sentAt: string | undefined, picked: boolean, now: number): "idle" | "detecting" | "opened" | "no_case" {
  const sent = Date.parse(sentAt ?? "");
  if (!Number.isFinite(sent)) return "idle";
  if (picked) return "opened";
  return now - sent > GIVE_UP_MS ? "no_case" : "detecting";
}
