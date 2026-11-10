"use server";

import { redirect } from "next/navigation";
import { CoreError, core } from "@/lib/core";
import { latestCase, markOf } from "@/lib/ramp-portal";

export type ChangeState = { error?: string };

// The Ramp (mock) portal's submit (spec 0077). It forwards the clerk's change to the core under their
// own token; the core checks the numbers and who may send it, then makes the change in the Ramp mock
// as their linked Ramp user. A refusal returns the core's own sentence. The account number is never
// put in the URL, a log or the returned state. The URL carries the vendor's newest case and version
// at the moment of sending, so the page knows when Dialback has picked this change up.
export async function sendBankChange(_: ChangeState, form: FormData): Promise<ChangeState> {
  const datasetId = String(form.get("dataset_id") ?? "");
  const vendorId = String(form.get("vendor_id") ?? "");
  const routing = String(form.get("routing_number") ?? "").replace(/\s+/g, "");
  const account = String(form.get("account_number") ?? "").replace(/\s+/g, "");
  let base: string;
  try {
    base = markOf(latestCase(await core.allCases(datasetId), vendorId));
    await core.bankChange(datasetId, vendorId, { routing_number: routing, account_number: account });
  } catch (e) {
    if (e instanceof CoreError) return { error: e.body.message };
    throw e;
  }
  const sent = new Date().toISOString();
  redirect(`/d/${encodeURIComponent(datasetId)}/ramp/${encodeURIComponent(vendorId)}?sent=${encodeURIComponent(sent)}&base=${base}`);
}
