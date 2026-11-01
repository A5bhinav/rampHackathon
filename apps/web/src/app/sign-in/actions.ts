"use server";

import { redirect } from "next/navigation";
import { DEV_TOKEN_SECONDS, devAuthEnabled, mintDevToken } from "@/lib/dev-auth";
import { setSession } from "@/lib/session";

export async function signInAsDevUser(formData: FormData): Promise<void> {
  if (!devAuthEnabled()) redirect("/sign-in");
  const userId = String(formData.get("user_id") ?? "");
  await setSession(await mintDevToken(userId), DEV_TOKEN_SECONDS);
  redirect("/");
}
