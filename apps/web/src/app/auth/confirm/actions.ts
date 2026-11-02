"use server";

import { redirect } from "next/navigation";
import { authMode, linkParams } from "@/lib/auth-mode";
import { supabaseServer } from "@/lib/supabase";

/** Spends the link's one-time token and starts the session (Supabase writes its cookies). */
export async function confirmSignIn(formData: FormData): Promise<void> {
  const link = linkParams({ token_hash: formData.get("token_hash"), type: formData.get("type") });
  if (authMode(process.env) !== "supabase" || !link) redirect("/sign-in?failed=1");
  const { error } = await (await supabaseServer()).auth.verifyOtp({ type: link.type, token_hash: link.tokenHash });
  if (error) {
    console.warn(`sign-in link refused: ${error.status ?? "?"} ${error.code ?? error.name}`);
    redirect("/sign-in?failed=1");
  }
  redirect("/");
}
