"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { CoreError, core } from "@/lib/core";

export type ActionState = { error?: string };

// On success the case usually moves on (awaiting release -> releasing), so the console that
// sent the request is no longer shown. The outcome goes back to the page as a short code in
// the URL, and the page shows a templated sentence for it (actionNotice in lib/states.ts).
async function run(path: string, fn: () => Promise<{ status: string; to_state: string }>): Promise<ActionState> {
  let result: { status: string; to_state: string };
  try {
    result = await fn();
  } catch (e) {
    if (e instanceof CoreError) return { error: e.body.message };
    throw e;
  }
  revalidatePath(path);
  redirect(`${path}?done=${encodeURIComponent(result.status)}&to=${encodeURIComponent(result.to_state)}`);
}

function fields(formData: FormData) {
  return {
    caseId: String(formData.get("case_id")),
    path: String(formData.get("path")),
    expected_version: Number(formData.get("expected_version")),
    reason: String(formData.get("reason") ?? ""),
    action: String(formData.get("action")),
  };
}

// The database decides who may release (R5, ADR-9); these only forward the request under the
// signed-in person's own token, with the case version they saw.
export async function releaseAction(_: ActionState, formData: FormData): Promise<ActionState> {
  const f = fields(formData);
  if (f.action !== "approve" && f.action !== "reject") return { error: "Unknown action." };
  return run(f.path, () => core.release(f.caseId, { expected_version: f.expected_version, action: f.action as "approve" | "reject", reason: f.reason }));
}

export async function reviewAction(_: ActionState, formData: FormData): Promise<ActionState> {
  const f = fields(formData);
  if (f.action !== "clear" && f.action !== "block") return { error: "Unknown action." };
  return run(f.path, () => core.review(f.caseId, { expected_version: f.expected_version, action: f.action as "clear" | "block", reason: f.reason }));
}
