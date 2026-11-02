import "server-only";
import { cookies } from "next/headers";
import { authMode } from "./auth-mode";
import { supabaseServer } from "./supabase";

// The signed-in user's JWT, forwarded to the core, which verifies it and runs every query in
// Postgres as that person (spec 0002, D9; spec 0010). With Supabase it is the session's access
// token, from Supabase's httpOnly cookies; with the local dev sign-in, a token in our own cookie.
export const SESSION_COOKIE = "dialback_session";

export async function sessionToken(): Promise<string | null> {
  if (authMode(process.env) === "supabase") {
    // Read only to forward it: the core checks the signature, so nothing here trusts it.
    const { data } = await (await supabaseServer()).auth.getSession();
    return data.session?.access_token ?? null;
  }
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function setSession(token: string, maxAgeSeconds: number): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: maxAgeSeconds,
  });
}

/** Ends whichever session exists: Supabase's (revoking its refresh tokens) and the dev cookie.
 *  The browser's session ends even if Supabase can't be reached: its cookies are always deleted. */
export async function clearSession(): Promise<void> {
  const store = await cookies();
  if (authMode(process.env) === "supabase") {
    try {
      const { error } = await (await supabaseServer()).auth.signOut();
      if (error) console.warn(`sign-out did not reach Supabase: ${error.status ?? "?"} ${error.code ?? error.name}`);
    } catch (e) {
      console.warn(`sign-out did not reach Supabase: ${(e as Error).name}`);
    }
    for (const { name } of store.getAll()) {
      if (/^sb-.+-auth-token/.test(name)) store.delete(name);
    }
  }
  store.delete(SESSION_COOKIE);
}
