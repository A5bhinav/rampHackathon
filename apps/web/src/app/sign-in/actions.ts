"use server";

import { redirect } from "next/navigation";
import { after } from "next/server";
import { authMode, cleanEmail } from "@/lib/auth-mode";
import { DEV_TOKEN_SECONDS, devAuthEnabled, mintDevToken } from "@/lib/dev-auth";
import { setSession } from "@/lib/session";
import { supabaseStateless } from "@/lib/supabase";

export async function signInAsDevUser(formData: FormData): Promise<void> {
  if (!devAuthEnabled()) redirect("/sign-in");
  const userId = String(formData.get("user_id") ?? "");
  await setSession(await mintDevToken(userId), DEV_TOKEN_SECONDS);
  redirect("/");
}

/** Emails a magic link to an existing user (spec 0010). The answer, and how long it takes, are
 *  the same whether or not the address has an account: the link is requested after the page has
 *  answered, so the page can't be used to find out who has one. */
export async function sendMagicLink(formData: FormData): Promise<void> {
  if (authMode(process.env) !== "supabase") redirect("/sign-in");
  const email = cleanEmail(formData.get("email"));
  if (!email) redirect("/sign-in?invalid=1");
  after(async () => {
    const { error } = await supabaseStateless().auth.signInWithOtp({ email, options: { shouldCreateUser: false } });
    if (error) console.warn(`magic link not sent: ${error.status ?? "?"} ${error.code ?? error.name}`);  // never the address
  });
  redirect("/sign-in?sent=1");
}
