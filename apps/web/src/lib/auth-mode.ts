// Which sign-in the dashboard offers, the inputs it accepts, and how long a sign-in lasts
// (spec 0010). Pure, so it is unit-tested and safe to import from proxy.ts. Who someone is gets
// decided by the core, which verifies every token; nothing here grants access.

export type AuthMode = "dev" | "supabase" | "none";
type Env = Record<string, string | undefined>;

/** The local dev sign-in only off production; Supabase when its URL and publishable key are set. */
export function authMode(env: Env): AuthMode {
  if (env.DIALBACK_AUTH_MODE === "dev" && env.VERCEL_ENV !== "production") return "dev";
  if (env.SUPABASE_URL && env.SUPABASE_PUBLISHABLE_KEY) return "supabase";
  return "none";
}

// Supabase's session cookies, readable by the server only: the dashboard never runs Supabase in
// the browser, so no script there needs the tokens (@supabase/ssr defaults to readable ones).
export const SUPABASE_COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax",
  secure: process.env.NODE_ENV === "production",
  path: "/",
} as const;

/** How long one sign-in lasts (spec 0010, Q31): a working day. Then the person signs in again,
 *  even though Supabase would keep refreshing the session. */
export const SESSION_HOURS = 12;
export const SINCE_COOKIE = "dialback_signed_in_at";

/** Whether a sign-in made at `since` (an ISO time) is still within SESSION_HOURS. A minute of
 *  clock skew is allowed; anything further in the future is not trusted. */
export function sessionFresh(since: string | undefined, now: Date): boolean {
  if (!since || !/^\d{4}-\d{2}-\d{2}T/.test(since)) return false;
  const at = Date.parse(since);
  if (Number.isNaN(at) || at > now.getTime() + 60_000) return false;
  return now.getTime() - at <= SESSION_HOURS * 3_600_000;
}

/** When the person signed in, from the verified token's `amr` (seconds since the epoch), as an
 *  ISO time. The token is signed, so unlike a cookie the person can't move it. */
export function signedInAt(claims: unknown): string | undefined {
  const amr = (claims as { amr?: unknown } | null | undefined)?.amr;
  if (!Array.isArray(amr)) return undefined;
  const times = amr
    .map((entry) => (entry && typeof entry === "object" ? (entry as { timestamp?: unknown }).timestamp : undefined))
    .filter((t): t is number => typeof t === "number" && Number.isFinite(t));
  return times.length > 0 ? new Date(Math.max(...times) * 1000).toISOString() : undefined;
}

/** A few sign-in tries per client per window. Every sign-in reaches Supabase from our server, so
 *  without this one client could use up the rate limit everyone shares. Per server instance. */
export class Throttle {
  private readonly hits = new Map<string, number[]>();
  private readonly limit: number;
  private readonly windowMs: number;
  private readonly now: () => number;

  constructor(limit: number, windowMs: number, now: () => number = Date.now) {
    this.limit = limit;
    this.windowMs = windowMs;
    this.now = now;
  }

  allow(key: string): boolean {
    const t = this.now();
    const recent = (this.hits.get(key) ?? []).filter((at) => t - at < this.windowMs);
    const ok = recent.length < this.limit;
    if (ok) recent.push(t);
    if (this.hits.size > 10_000) this.hits.clear();  // bounded memory under a spray of addresses
    this.hits.set(key, recent);
    return ok;
  }
}

/** The password exactly as typed (never trimmed), or null if it can't be one. */
export function cleanPassword(raw: unknown): string | null {
  return typeof raw === "string" && raw.length > 0 && raw.length <= 256 ? raw : null;
}

// Self sign-up (spec 0027) ---------------------------------------------------------------------

/** A new password: at least this long, and at most 72 characters (Supabase's limit). */
export const MIN_NEW_PASSWORD = 8;

export function cleanNewPassword(raw: unknown): string | null {
  return typeof raw === "string" && raw.length >= MIN_NEW_PASSWORD && raw.length <= 72 ? raw : null;
}

/** The name a person signs up with: trimmed, 1 to 80 characters, no control characters. */
export function cleanName(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const name = raw.trim();
  return name.length >= 1 && name.length <= 80 && !/\p{Cc}/u.test(name) ? name : null;
}

export type SignUpNotice = "exists" | "weak" | "slow" | "unavailable" | "failed" | "confirm";

/** What the sign-up page says about Supabase's answer, or null when the person is now signed in.
 *  Only a returned session signs anyone in; an account created without one is left alone. */
export function signUpNotice(error: { status?: number; code?: string } | null, hasSession: boolean): SignUpNotice | null {
  if (error) {
    if (error.code === "user_already_exists" || error.code === "email_exists") return "exists";
    if (error.code === "weak_password") return "weak";
    if (error.status === 429) return "slow";
    if (!error.status || error.status >= 500) return "unavailable";
    return "failed";
  }
  return hasSession ? null : "confirm";
}

// One-click demo accounts (spec 0034) ----------------------------------------------------------

export type DemoRole = "clerk" | "approver";
export type DemoAccounts = Record<DemoRole, { email: string; password: string }>;

/** The shared demo accounts, only when DIALBACK_DEMO_SIGN_IN=on, in Supabase mode, and both are
 *  fully set; anything else is null and no button is shown. The passwords stay on the server. The
 *  database lets these accounts act only on demo datasets, whatever this returns. */
export function demoAccounts(env: Env): DemoAccounts | null {
  if (env.DIALBACK_DEMO_SIGN_IN !== "on" || authMode(env) !== "supabase") return null;
  const one = (role: string) => {
    const email = cleanEmail(env[`DIALBACK_DEMO_${role}_EMAIL`]);
    const password = cleanPassword(env[`DIALBACK_DEMO_${role}_PASSWORD`]);
    return email && password ? { email, password } : null;
  };
  const clerk = one("CLERK");
  const approver = one("APPROVER");
  return clerk && approver ? { clerk, approver } : null;
}

export function cleanEmail(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const email = raw.trim().toLowerCase();
  return email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : null;
}
