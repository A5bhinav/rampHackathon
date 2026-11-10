"use server";

import { redirect } from "next/navigation";
import { CoreError, core } from "@/lib/core";
import { accountValid, routingValid } from "@/lib/ramp-portal";

export type ChangeState = { error?: string };

// The Ramp (mock) portal's submit (spec 0077). It forwards the clerk's change to the core under their
// own token; the core sends it to the Ramp mock as their linked Ramp user. The account number is
// never put in the URL, a log or the returned state; a refusal returns the core's own sentence.
export async function sendBankChange(_: ChangeState, form: FormData): Promise<ChangeState> {
  const datasetId = String(form.get("dataset_id") ?? "");
  const vendorId = String(form.get("vendor_id") ?? "");
  const routing = String(form.get("routing_number") ?? "").replace(/\s+/g, "");
  const account = String(form.get("account_number") ?? "").replace(/\s+/g, "");
  if (!routingValid(routing)) return { error: "Enter a nine-digit routing number with a valid checksum." };
  if (!accountValid(account)) return { error: "Enter an account number of 4 to 17 digits." };
  let sentAt: string;
  try {
    sentAt = new Date().toISOString();
    await core.bankChange(datasetId, vendorId, { routing_number: routing, account_number: account });
  } catch (e) {
    if (e instanceof CoreError) return { error: e.body.message };
    throw e;
  }
  redirect(`/d/${datasetId}/ramp/${vendorId}?sent=${encodeURIComponent(sentAt)}`);
}
