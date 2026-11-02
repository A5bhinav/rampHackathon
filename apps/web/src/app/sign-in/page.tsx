import { devAuthEnabled, devUsers } from "@/lib/dev-auth";
import { signInAsDevUser } from "./actions";

export default async function SignIn({ searchParams }: { searchParams: Promise<{ expired?: string }> }) {
  const { expired } = await searchParams;
  const users = devUsers();
  return (
    <main className="shell">
      <div className="card signin">
        <h1>Sign in to Dialback</h1>
        {expired ? <p className="result error">Your session ended. Sign in again.</p> : null}
        {devAuthEnabled() ? (
          users.length > 0 ? (
            <form action={signInAsDevUser}>
              <p className="muted">Local development: choose a seeded user. The core verifies this token exactly as it would a Supabase one.</p>
              <label htmlFor="user_id">User</label>
              <select id="user_id" name="user_id" required>
                {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
              <button className="primary" type="submit">Sign in</button>
            </form>
          ) : (
            <p className="muted">Set <span className="mono">DIALBACK_DEV_USERS</span> to the seeded users (<span className="mono">uuid:Name,uuid:Name</span>).</p>
          )
        ) : (
          <p className="muted">Supabase sign-in is not configured yet. For local development, set <span className="mono">DIALBACK_AUTH_MODE=dev</span>.</p>
        )}
      </div>
    </main>
  );
}
