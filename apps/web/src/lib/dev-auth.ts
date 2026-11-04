import "server-only";
import { SignJWT } from "jose";
import { authMode } from "./auth-mode";

// Local development without Supabase (spec 0002, D9). The dashboard's server mints a
// short-lived JWT for a seeded user with a dev secret, which the core accepts only when it is
// given that secret and no Supabase project (spec 0010). Never available in a production deployment.

export type DevUser = { id: string; name: string };

export function devAuthEnabled(): boolean {
  return authMode(process.env) === "dev";
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
