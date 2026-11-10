// The demo's Ramp (mock) portal (spec 0077). Display and form checks only: the core checks the
// numbers again, decides who may send a change, and never stores the full account number.

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

const SLACK_MS = 30_000;  // the dashboard's clock and the core's can differ by a few seconds

/** Whether the case has picked up a change sent at `sentAt`: a detection at or after it. */
export function pickedUp(timeline: readonly { type: string; occurred_at: string }[], sentAt: string): boolean {
  const sent = Date.parse(sentAt);
  return Number.isFinite(sent) && timeline.some((e) => e.type === "change_detected" && Date.parse(e.occurred_at) >= sent - SLACK_MS);
}

/** What the vendor page shows after a send: nothing sent, waiting for Dialback, or the case it opened. */
export function watchFor(sentAt: string | null, found: { picked: boolean } | null): "idle" | "detecting" | "opened" {
  if (!sentAt || !Number.isFinite(Date.parse(sentAt))) return "idle";
  return found?.picked ? "opened" : "detecting";
}
