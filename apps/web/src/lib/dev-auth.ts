import "server-only";
import { SignJWT } from "jose";

// Local development without Supabase (spec 0002, D9). The dashboard's server mints a
// short-lived JWT for a seeded user with a dev secret; the core verifies it with exactly the
// code it uses for Supabase tokens. Never available in a production deployment.

export type DevUser = { id: string; name: string };

export function devAuthEnabled(): boolean {
  return process.env.DIALBACK_AUTH_MODE === "dev" && process.env.VERCEL_ENV !== "production";
}

// DIALBACK_DEV_USERS="<uuid>:Pat Clerk,<uuid>:Riley Approver"
export function devUsers(): DevUser[] {
  return (process.env.DIALBACK_DEV_USERS ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const [id, ...name] = entry.split(":");
      return { id: id.trim(), name: name.join(":").trim() || id.trim() };
    });
}

export const DEV_TOKEN_SECONDS = 60 * 60;

export async function mintDevToken(userId: string): Promise<string> {
  if (!devAuthEnabled()) throw new Error("dev sign-in is disabled");
  if (!devUsers().some((u) => u.id === userId)) throw new Error("not a configured dev user");
  const secret = process.env.DIALBACK_JWT_SECRET ?? "";
  if (secret.length < 32) throw new Error("DIALBACK_JWT_SECRET must be set (32+ characters) for dev sign-in");
  return new SignJWT({ role: "authenticated" })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(userId)
    .setAudience("authenticated")
    .setIssuedAt()
    .setExpirationTime(`${DEV_TOKEN_SECONDS}s`)
    .sign(new TextEncoder().encode(secret));
}
