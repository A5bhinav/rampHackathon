import "server-only";
import { cookies } from "next/headers";
import { SESSION_HOURS, SINCE_COOKIE, authMode, sessionFresh } from "./auth-mode";
import { supabaseServer } from "./supabase";

// The signed-in user's JWT, forwarded to the core, which verifies it and runs every query in
// Postgres as that person (spec 0002, D9; spec 0010). With Supabase it is the session's access
// token, from Supabase's httpOnly cookies; with the local dev sign-in, a token in our own cookie.
// A Supabase sign-in lasts SESSION_HOURS, counted from the moment the person signed in.
export const SESSION_COOKIE = "dialback_session";

export async function sessionToken(): Promise<string | null> {
  const store = await cookies();
  if (authMode(process.env) === "supabase") {
    if (!sessionFresh(store.get(SINCE_COOKIE)?.value, new Date())) return null;
    // Read only to forward it: the core checks the signature, so nothing here trusts it.
    const { data } = await (await supabaseServer()).auth.getSession();
    return data.session?.access_token ?? null;
  }
  return store.get(SESSION_COOKIE)?.value ?? null;
}

/** Records when the person signed in, so the session ends SESSION_HOURS later. */
export async function markSignedIn(): Promise<void> {
  (await cookies()).set(SINCE_COOKIE, new Date().toISOString(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_HOURS * 3600,
  });
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

/** Ends whichever session exists: this browser's Supabase session (only this one: the judges
 *  share accounts) and the dev cookie. The browser's session ends even if Supabase can't be
 *  reached: its cookies are always deleted. */
export async function clearSession(): Promise<void> {
  const store = await cookies();
  if (authMode(process.env) === "supabase") {
    try {
      const { error } = await (await supabaseServer()).auth.signOut({ scope: "local" });
      if (error) console.warn(`sign-out did not reach Supabase: ${error.status ?? "?"} ${error.code ?? error.name}`);
    } catch (e) {
      console.warn(`sign-out did not reach Supabase: ${(e as Error).name}`);
    }
    for (const { name } of store.getAll()) {
      if (/^sb-.+-auth-token/.test(name)) store.delete(name);
    }
  }
  store.delete(SINCE_COOKIE);
  store.delete(SESSION_COOKIE);
}
