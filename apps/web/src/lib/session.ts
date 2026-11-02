import "server-only";
import { cookies } from "next/headers";

// The signed-in user's JWT, kept in an httpOnly cookie and forwarded to the core, which
// verifies it and runs every query in Postgres as that person (spec 0002, D9).
export const SESSION_COOKIE = "dialback_session";

export async function sessionToken(): Promise<string | null> {
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

export async function clearSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}
