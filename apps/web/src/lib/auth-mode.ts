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

/** Whether a sign-in made at `since` (an ISO time we wrote) is still within SESSION_HOURS. */
export function sessionFresh(since: string | undefined, now: Date): boolean {
  if (!since || !/^\d{4}-\d{2}-\d{2}T/.test(since)) return false;
  const at = Date.parse(since);
  if (Number.isNaN(at) || at > now.getTime()) return false;
  return now.getTime() - at <= SESSION_HOURS * 3_600_000;
}

/** The password exactly as typed (never trimmed), or null if it can't be one. */
export function cleanPassword(raw: unknown): string | null {
  return typeof raw === "string" && raw.length > 0 && raw.length <= 256 ? raw : null;
}

export function cleanEmail(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const email = raw.trim().toLowerCase();
  return email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : null;
}
