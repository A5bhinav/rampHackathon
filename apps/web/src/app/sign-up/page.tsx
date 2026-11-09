import Link from "next/link";
import { redirect } from "next/navigation";
import { MIN_NEW_PASSWORD, PASSWORD_RULE, authMode, type SignUpNotice } from "@/lib/auth-mode";
import { signUp } from "./actions";
import { BrandMark } from "@/components/nav";

type Params = Partial<Record<SignUpNotice, string>>;

const NOTICES: [SignUpNotice, string][] = [
  ["exists", "An account with that email already exists. Sign in instead."],
  ["weak", `Choose a stronger password: ${PASSWORD_RULE}.`],
  ["failed", "Check your name and email, then try again."],
  ["slow", "Too many attempts. Wait a few minutes, then try again."],
  ["unavailable", "Sign-up isn't available right now. Try again in a minute."],
  ["confirm", "Your account was created but must be confirmed before you can sign in. Ask the Dialback team."],
];

// Self sign-up (spec 0027). A new account is an employee account with the clerk role; the team
// grants approver, which is what releases payments.
export default async function SignUp({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;  // first, so the page renders per request, never at build
  if (authMode(process.env) !== "supabase") redirect("/sign-in");
  return (
    <main className="shell">
      <div className="card signin">
        <p className="brand"><BrandMark />Dialback</p>
        <h1>Create your account</h1>
        {NOTICES.filter(([key]) => params[key]).map(([key, text]) => (
          <p key={key} className="result error" role="alert">{text}</p>
        ))}
        <form action={signUp}>
          <label htmlFor="name">Name</label>
          <input id="name" name="name" type="text" autoComplete="name" maxLength={80} required />
          <label htmlFor="email">Email</label>
          <input id="email" name="email" type="email" autoComplete="username" required />
          <label htmlFor="password">Password</label>
          <input id="password" name="password" type="password" autoComplete="new-password"
                 minLength={MIN_NEW_PASSWORD} maxLength={72} required />
          <p className="muted sub field-hint">{PASSWORD_RULE[0].toUpperCase() + PASSWORD_RULE.slice(1)}.</p>
          <button className="primary" type="submit">Create account</button>
        </form>
        <p className="muted">Already have an account? <Link href="/sign-in">Sign in</Link></p>
      </div>
    </main>
  );
}
