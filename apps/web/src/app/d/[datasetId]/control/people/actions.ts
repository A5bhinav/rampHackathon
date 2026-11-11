"use server";

import { revalidatePath } from "next/cache";
import { CoreError, core } from "@/lib/core";
import { ROLES } from "@/lib/people";
import type { Role } from "@/lib/api-types";

export type PeopleState = { error?: string; done?: string };

// The admin's People page (spec 0095). Each action forwards one change to the core under the admin's own
// token; the database decides (D3) and its refusal comes back word for word. Nothing is decided here.
async function run(path: string, done: string, fn: () => Promise<unknown>): Promise<PeopleState> {
  try {
    await fn();
  } catch (e) {
    if (e instanceof CoreError) return { error: e.body.message };
    throw e;
  }
  revalidatePath(path);
  return { done };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? "");

export async function roleAction(_: PeopleState, form: FormData): Promise<PeopleState> {
  const role = text(form, "role") as Role;
  if (!ROLES.includes(role)) return { error: "Pick a role." };
  const grant = text(form, "grant") === "true";
  return run(text(form, "path"), grant ? `Granted ${role}.` : `Took away ${role}.`,
    () => core.changeRole(text(form, "user_id"), { role, grant, reason: text(form, "reason") }));
}

export async function rampLinkAction(_: PeopleState, form: FormData): Promise<PeopleState> {
  const ramp = text(form, "ramp_user_id").trim();
  return run(text(form, "path"), ramp ? "Linked." : "Link cleared.",
    () => core.changeRampLink(text(form, "user_id"), { ramp_user_id: ramp || null, reason: text(form, "reason") }));
}

export async function accessAction(_: PeopleState, form: FormData): Promise<PeopleState> {
  const enabled = text(form, "enabled") === "true";
  return run(text(form, "path"), enabled ? "Access turned on." : "Access turned off.",
    () => core.changeAccess(text(form, "user_id"), { enabled, reason: text(form, "reason") }));
}
