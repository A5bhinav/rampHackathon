import type { HoldStatus } from "@/lib/api-types";
import { holdGate } from "@/lib/flow";
import { date } from "@/lib/format";
import { Icon } from "./icons";

/** The payment's path on a case (spec 0032): from the company's Ramp account, through the hold,
 *  to the vendor's new account, with the old one it replaces. Routing numbers and last four
 *  digits only (ADR-8); the hold's state is the core's, shown, never inferred. */
export function PaymentPath({ bills, nextPayment, holdStatus, newAccount, oldAccount }: {
  bills: number;
  nextPayment: string | null;
  holdStatus: HoldStatus | null;
  newAccount: string;
  oldAccount: string | null;
}) {
  const gate = holdGate(holdStatus);
  return (
    <ol className="path" aria-label="Where the payments go">
      <li className="path-stop">
        <span className="path-node"><Icon name="card" /></span>
        <span className="path-text">
          <span className="path-label">Your company, in Ramp</span>
          <span className="muted sub">{bills} unpaid bill{bills === 1 ? "" : "s"}{nextPayment ? `, next due ${date(nextPayment)}` : ""}</span>
        </span>
      </li>
      <li className={`path-gate is-${gate.kind}`}>
        <span className="path-node"><Icon name={gate.kind === "lifted" ? "release" : "hold"} size={16} /></span>
        <span className="path-gate-text">{gate.text}</span>
      </li>
      <li className="path-stop is-new">
        <span className="path-node"><Icon name="bank" /></span>
        <span className="path-text">
          <span className="path-label">New account</span>
          <span className="mono path-account">{newAccount}</span>
          <span className="muted sub">{oldAccount ? <>Replaces <span className="mono">{oldAccount}</span></> : "No account on file before"}</span>
        </span>
      </li>
    </ol>
  );
}
