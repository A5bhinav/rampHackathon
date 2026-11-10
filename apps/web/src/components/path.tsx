import type { HoldStatus } from "@/lib/api-types";
import { holdGate } from "@/lib/flow";
import { date } from "@/lib/format";

const GATE_TONE = { held: "info", lifted: "ok", placing: "muted", failed: "bad" } as const;

/** The payment's path on a case (specs 0032, 0041): the account it used to go to, the new one,
 *  and the hold between them. Routing numbers and last four digits only (ADR-8); the hold's
 *  state is the core's, shown, never inferred. */
export function PaymentPath({ bills, nextPayment, holdStatus, newAccount, oldAccount }: {
  bills: number;
  nextPayment: string | null;
  holdStatus: HoldStatus | null;
  newAccount: string;
  oldAccount: string | null;
}) {
  const gate = holdGate(holdStatus);
  return (
    <div className="path-wrap">
      <div className="path" role="group" aria-label="Where the payments go">
        <div className="acct acct-old">
          <span className="acct-label">Previous account</span>
          <span className="mono acct-number">{oldAccount ?? "None on file"}</span>
        </div>
        <span className="acct-arrow" aria-hidden="true">→</span>
        <div className="acct acct-new">
          <span className="acct-label">New account</span>
          <span className="mono acct-number">{newAccount}</span>
        </div>
      </div>
      <p className="path-gate">
        <span className={`badge tone-${GATE_TONE[gate.kind]}`}>{gate.text}</span>
        <span className="muted sub">{bills} unpaid bill{bills === 1 ? "" : "s"}{nextPayment ? `, next due ${date(nextPayment)}` : ""}</span>
      </p>
    </div>
  );
}
