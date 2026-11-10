import Link from "next/link";
import { authMode, demoAccounts } from "@/lib/auth-mode";
import { devUsers } from "@/lib/dev-auth";
import { signIn, signInAsDemo, signInAsDevUser } from "./actions";
import { FrontDoor } from "@/components/front-door";
import { safeNext } from "@/lib/story";

type Notice = { expired?: string; failed?: string; slow?: string; unavailable?: string; next?: string };

const NOTICES: [keyof Notice, string][] = [
  ["expired", "Your session ended. Sign in again."],
  ["failed", "That email and password don't match a Dialback account."],
  ["slow", "Too many sign-in attempts. Wait a few minutes, then try again."],
  ["unavailable", "Sign-in isn't available right now. Try again in a minute."],
];

export default async function SignIn({ searchParams }: { searchParams: Promise<Notice> }) {
  const params = await searchParams;
  const mode = authMode(process.env);
  const next = safeNext(params.next);  // a demo link to return to (spec 0033); anything else is dropped
  return (
    <FrontDoor title="Sign in">
        <p className="muted front-lead">Payments to a vendor whose bank details changed stay held until the vendor confirms.</p>
        {NOTICES.filter(([key]) => params[key]).map(([key, text]) => (
          <p key={key} className="result error" role="alert">{text}</p>
        ))}
        {mode === "supabase" && demoAccounts(process.env) ? (
          <form action={signInAsDemo} className="demo-sign-in">
            <input type="hidden" name="next" value={next} />
            <h2>Look around with a demo account</h2>
            <div className="demo-buttons">
              <button className="primary" type="submit" name="role" value="approver">Sign in as the demo approver</button>
              <p className="muted sub">Releases payments, with a written reason. Not the person who entered the change.</p>
              <button type="submit" name="role" value="clerk">Sign in as the demo clerk</button>
              <p className="muted sub">Entered the demo&apos;s bank changes, so the database refuses their release (R5).</p>
            </div>
            <p className="muted sub">Shared accounts on fictional demo data only; the database refuses them anywhere else.</p>
          </form>
        ) : null}
        {mode === "supabase" && demoAccounts(process.env) ? <p className="or" role="separator"><span>or</span></p> : null}
        {mode === "supabase" ? (
          <form action={signIn}>
            <input type="hidden" name="next" value={next} />
            <p className="muted">Sign in with your Dialback account.</p>
            <label htmlFor="email">Email</label>
            <input id="email" name="email" type="email" autoComplete="username" required />
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password" required />
            <button className="primary" type="submit">Sign in</button>
            <p className="muted">New to Dialback? <Link href="/sign-up">Create an account</Link></p>
          </form>
        ) : mode === "dev" ? <DevSignIn next={next} /> : (
          <p className="muted">
            Sign-in is not configured. Set <span className="mono">SUPABASE_URL</span> and{" "}
            <span className="mono">SUPABASE_PUBLISHABLE_KEY</span>, or for local development{" "}
            <span className="mono">DIALBACK_AUTH_MODE=dev</span>.
          </p>
        )}
    </FrontDoor>
  );
}

function DevSignIn({ next }: { next: string }) {
  const users = devUsers();
  return users.length > 0 ? (
    <form action={signInAsDevUser}>
      <input type="hidden" name="next" value={next} />
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
