"use server";

import { revalidatePath } from "next/cache";
import { CoreError, core } from "@/lib/core";

export type DemoActionState = { error?: string; done?: string };

// The admin's demo controls (spec 0095, D9). The core checks the admin and puts the intent on the
// organisation chain before it runs anything; a refusal comes back word for word.
async function run(path: string, done: string, fn: () => Promise<unknown>): Promise<DemoActionState> {
  try {
    await fn();
  } catch (e) {
    if (e instanceof CoreError) return { error: e.body.message };
    throw e;
  }
  revalidatePath(path, "layout");
  return { done };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? "");

export async function resetAction(_: DemoActionState, form: FormData): Promise<DemoActionState> {
  const clerk = text(form, "link_clerk_user_id");
  return run(text(form, "path"), "A fresh demo is ready. Switch to it from the dataset menu.",
    () => core.demoReset({ link_clerk_user_id: clerk || null, reason: text(form, "reason") }));
}

export async function chaosAction(_: DemoActionState, form: FormData): Promise<DemoActionState> {
  const enabled = text(form, "enabled") === "true";
  return run(text(form, "path"), enabled ? "Chaos is on." : "Chaos is off.", () => core.demoChaos({ enabled, reason: text(form, "reason") }));
}

export async function tamperAction(_: DemoActionState, form: FormData): Promise<DemoActionState> {
  if (text(form, "confirm") !== "break the chain") return { error: "Type break the chain to confirm." };
  return run(text(form, "path"), "One row of the demo's chain was rewritten. The Evidence page shows where it breaks.",
    () => core.demoTamper({ confirm: "break the chain", reason: text(form, "reason") }));
}
