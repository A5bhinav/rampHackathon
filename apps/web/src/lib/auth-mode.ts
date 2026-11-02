// Which sign-in the dashboard offers, and the inputs it accepts (spec 0010). Pure, so it is
// unit-tested and safe to import from proxy.ts. Who someone is gets decided by the core, which
// verifies every token; nothing here grants access.

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

/** The link types /auth/confirm accepts: a magic link and an invite. Never a recovery or email change. */
export type LinkType = "email" | "invite";

export function linkParams(params: { token_hash?: unknown; type?: unknown }): { tokenHash: string; type: LinkType } | null {
  const { token_hash: tokenHash, type } = params;
  if (typeof tokenHash !== "string" || !/^[A-Za-z0-9_-]{16,512}$/.test(tokenHash)) return null;
  if (type !== "email" && type !== "invite") return null;
  return { tokenHash, type };
}

export function cleanEmail(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const email = raw.trim().toLowerCase();
  return email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : null;
}
