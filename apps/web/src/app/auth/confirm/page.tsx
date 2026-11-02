import type { Metadata } from "next";
import Link from "next/link";
import { authMode, linkParams } from "@/lib/auth-mode";
import { confirmSignIn } from "./actions";

// Where a magic link or an invite lands (spec 0010). Opening the link signs nobody in: email
// scanners open links too, and the token works once. The person presses the button, and only
// then is the token spent. The token never leaves this page through a referrer.
export const metadata: Metadata = { title: "Sign in to Dialback", referrer: "no-referrer" };

export default async function Confirm({ searchParams }: { searchParams: Promise<{ token_hash?: string; type?: string }> }) {
  const link = linkParams(await searchParams);
  const ok = authMode(process.env) === "supabase" && link !== null;
  return (
    <main className="shell">
      <div className="card signin">
        <h1>Sign in to Dialback</h1>
        {ok ? (
          <form action={confirmSignIn}>
            <p className="muted">
              {link.type === "invite" ? "You were invited to Dialback." : "You asked for a sign-in link."} Press the button to finish
              signing in on this device.
            </p>
            <input type="hidden" name="token_hash" value={link.tokenHash} />
            <input type="hidden" name="type" value={link.type} />
            <button className="primary" type="submit">Sign in</button>
          </form>
        ) : (
          <>
            <p className="result error" role="alert">This sign-in link is not valid.</p>
            <p><Link href="/sign-in">Ask for a new link</Link></p>
          </>
        )}
      </div>
    </main>
  );
}
