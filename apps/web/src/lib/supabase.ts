import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { SUPABASE_COOKIE_OPTIONS } from "./auth-mode";

// Supabase Auth on the dashboard's server (spec 0010): password sign-in, the session cookies, sign-out.
// The browser never talks to Supabase, and the dashboard never decides who someone is: it hands
// the session's access token to the core, which verifies it against the project's keys.

export function supabaseConfig(): { url: string; key: string } {
  return { url: process.env.SUPABASE_URL ?? "", key: process.env.SUPABASE_PUBLISHABLE_KEY ?? "" };
}

export async function supabaseServer() {
  const store = await cookies();
  const { url, key } = supabaseConfig();
  return createServerClient(url, key, {
    cookieOptions: SUPABASE_COOKIE_OPTIONS,
    cookies: {
      getAll: () => store.getAll(),
      setAll: (toSet) => {
        try {
          toSet.forEach(({ name, value, options }) => store.set(name, value, options));
        } catch {
          // A Server Component can't set cookies. proxy.ts refreshes the session on every request.
        }
      },
    },
  });
}
