import { authMode } from "@/lib/auth-mode";
import { devUsers } from "@/lib/dev-auth";
import { sendMagicLink, signInAsDevUser } from "./actions";

type Notice = { expired?: string; sent?: string; failed?: string; invalid?: string };

const NOTICES: [keyof Notice, "ok" | "error", string][] = [
  ["expired", "error", "Your session ended. Sign in again."],
  ["sent", "ok", "If this address belongs to a Dialback user, a sign-in link is on its way. It works once; you can ask for another after a minute."],
  ["failed", "error", "That sign-in link is not valid any more. Links work once and expire; ask for a new one."],
  ["invalid", "error", "Enter a valid email address."],
];

export default async function SignIn({ searchParams }: { searchParams: Promise<Notice> }) {
  const params = await searchParams;
  const mode = authMode(process.env);
  return (
    <main className="shell">
      <div className="card signin">
        <h1>Sign in to Dialback</h1>
        {NOTICES.filter(([key]) => params[key]).map(([key, tone, text]) => (
          <p key={key} className={`result ${tone}`} role={tone === "error" ? "alert" : "status"}>{text}</p>
        ))}
        {mode === "supabase" ? (
          <form action={sendMagicLink}>
            <p className="muted">We email you a one-time sign-in link. Only people already invited to Dialback can sign in.</p>
            <label htmlFor="email">Work email</label>
            <input id="email" name="email" type="email" autoComplete="email" required />
            <button className="primary" type="submit">Email me a sign-in link</button>
          </form>
        ) : mode === "dev" ? <DevSignIn /> : (
          <p className="muted">
            Sign-in is not configured. Set <span className="mono">SUPABASE_URL</span> and{" "}
            <span className="mono">SUPABASE_PUBLISHABLE_KEY</span>, or for local development{" "}
            <span className="mono">DIALBACK_AUTH_MODE=dev</span>.
          </p>
        )}
      </div>
    </main>
  );
}

function DevSignIn() {
  const users = devUsers();
  return users.length > 0 ? (
    <form action={signInAsDevUser}>
      <p className="muted">Local development: choose a seeded user. The core accepts this token only when it has no Supabase project.</p>
      <label htmlFor="user_id">User</label>
      <select id="user_id" name="user_id" required>
        {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </select>
      <button className="primary" type="submit">Sign in</button>
    </form>
  ) : (
    <p className="muted">Set <span className="mono">DIALBACK_DEV_USERS</span> to the seeded users (<span className="mono">uuid:Name,uuid:Name</span>).</p>
  );
}
