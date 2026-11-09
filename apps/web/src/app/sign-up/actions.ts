"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { Throttle, authMode, cleanEmail, cleanName, cleanNewPassword, signUpNotice } from "@/lib/auth-mode";
import { markSignedIn } from "@/lib/session";
import { supabaseServer } from "@/lib/supabase";

// The same budget as sign-in: ten tries per client per five minutes (spec 0027, decision 4).
const attempts = new Throttle(10, 5 * 60_000);

/** Creates an account and signs the person in (spec 0027). The account's profile and clerk role
 *  come from the database's trigger on auth.users; nothing here grants anything. */
export async function signUp(formData: FormData): Promise<void> {
  if (authMode(process.env) !== "supabase") redirect("/sign-in");
  const client = (await headers()).get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  if (!attempts.allow(client)) redirect("/sign-up?slow=1");
  const name = cleanName(formData.get("name"));
  const email = cleanEmail(formData.get("email"));
  if (!name || !email) redirect("/sign-up?failed=1");
  const password = cleanNewPassword(formData.get("password"));
  if (!password) redirect("/sign-up?weak=1");
  const { data, error } = await (await supabaseServer()).auth.signUp({
    email,
    password,
    options: { data: { display_name: name } },
  });
  if (error) console.warn(`sign-up refused: ${error.status ?? "?"} ${error.code ?? error.name}`);  // never the address
  const notice = signUpNotice(error ? { status: error.status, code: error.code } : null, Boolean(data?.session));
  if (notice) redirect(`/sign-up?${notice}=1`);
  await markSignedIn();
  redirect("/");
}
