import { authMode } from "@/lib/auth-mode";
import { devUsers } from "@/lib/dev-auth";
import { signIn, signInAsDevUser } from "./actions";

type Notice = { expired?: string; failed?: string; slow?: string; unavailable?: string };

const NOTICES: [keyof Notice, string][] = [
  ["expired", "Your session ended. Sign in again."],
  ["failed", "That email and password don't match a Dialback account."],
  ["slow", "Too many sign-in attempts. Wait a few minutes, then try again."],
  ["unavailable", "Sign-in isn't available right now. Try again in a minute."],
];

export default async function SignIn({ searchParams }: { searchParams: Promise<Notice> }) {
  const params = await searchParams;
  const mode = authMode(process.env);
  return (
    <main className="shell">
      <div className="card signin">
        <h1>Sign in to Dialback</h1>
        {NOTICES.filter(([key]) => params[key]).map(([key, text]) => (
          <p key={key} className="result error" role="alert">{text}</p>
        ))}
        {mode === "supabase" ? (
          <form action={signIn}>
            <p className="muted">Use the account the Dialback team gave you.</p>
            <label htmlFor="email">Email</label>
            <input id="email" name="email" type="email" autoComplete="username" required />
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password" required />
            <button className="primary" type="submit">Sign in</button>
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
