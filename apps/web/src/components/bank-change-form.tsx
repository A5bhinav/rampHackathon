"use client";

import { useActionState } from "react";
import { sendBankChange, type ChangeState } from "@/app/d/[datasetId]/(ap)/ramp/[vendorId]/actions";

// The Ramp (mock) payment-details form (spec 0077). The fields are plain inputs the browser never
// remembers; the server action sends them to the core and keeps nothing.
export function BankChangeForm({ datasetId, vendorId }: { datasetId: string; vendorId: string }) {
  const [state, submit, pending] = useActionState<ChangeState, FormData>(sendBankChange, {});
  return (
    <form className="ramp-form" action={submit} autoComplete="off">
      <input type="hidden" name="dataset_id" value={datasetId} />
      <input type="hidden" name="vendor_id" value={vendorId} />
      <label htmlFor="routing_number">Routing number</label>
      <input id="routing_number" name="routing_number" type="text" inputMode="numeric" autoComplete="off" required
             maxLength={9} pattern="[0-9]{9}" placeholder="9 digits" />
      <label htmlFor="account_number">Account number</label>
      <input id="account_number" name="account_number" type="text" inputMode="numeric" autoComplete="off" required
             maxLength={17} pattern="[0-9]{4,17}" placeholder="4 to 17 digits" />
      <p className="muted sub">This is the Ramp mock: use a made-up account number.</p>
      {state.error ? (
        <div className="callout callout-warn" role="alert">
          <div className="callout-body"><p>{state.error}</p></div>
        </div>
      ) : null}
      <div className="actions">
        <button type="submit" className="primary" disabled={pending}>{pending ? "Saving…" : "Save payment details"}</button>
      </div>
    </form>
  );
}
