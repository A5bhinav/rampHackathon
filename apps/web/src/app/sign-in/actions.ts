"use server";

import { redirect } from "next/navigation";
import { authMode, cleanEmail, cleanPassword } from "@/lib/auth-mode";
import { DEV_TOKEN_SECONDS, devAuthEnabled, mintDevToken } from "@/lib/dev-auth";
import { markSignedIn, setSession } from "@/lib/session";
import { supabaseServer } from "@/lib/supabase";

export async function signInAsDevUser(formData: FormData): Promise<void> {
  if (!devAuthEnabled()) redirect("/sign-in");
  const userId = String(formData.get("user_id") ?? "");
  await setSession(await mintDevToken(userId), DEV_TOKEN_SECONDS);
  redirect("/");
}

/** Email and password, for accounts the team created (spec 0010). A wrong password and an
 *  unknown email get the same answer, so the page can't be used to find out who has an account. */
export async function signIn(formData: FormData): Promise<void> {
  if (authMode(process.env) !== "supabase") redirect("/sign-in");
  const email = cleanEmail(formData.get("email"));
  const password = cleanPassword(formData.get("password"));
  if (!email || !password) redirect("/sign-in?failed=1");
  const { error } = await (await supabaseServer()).auth.signInWithPassword({ email, password });
  if (error) {
    console.warn(`sign-in refused: ${error.status ?? "?"} ${error.code ?? error.name}`);  // never the address
    redirect(error.status === 429 ? "/sign-in?slow=1" : "/sign-in?failed=1");
  }
  await markSignedIn();
  redirect("/");
}
