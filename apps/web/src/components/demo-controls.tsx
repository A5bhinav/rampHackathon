"use client";

import { useActionState } from "react";
import { chaosAction, resetAction, tamperAction, type DemoActionState } from "@/app/d/[datasetId]/control/demo/actions";

type Action = (state: DemoActionState, form: FormData) => Promise<DemoActionState>;

function Control({ action, path, title, hint, children }: {
  action: Action; path: string; title: string; hint: string; children: React.ReactNode;
}) {
  const [state, submit, pending] = useActionState(action, {});
  const id = title.replace(/\W+/g, "-").toLowerCase();
  return (
    <form className="card-body admin-change" action={submit}>
      <input type="hidden" name="path" value={path} />
      <fieldset disabled={pending}>
        <legend>{title}</legend>
        <p className="muted sub">{hint}</p>
        {children}
        <label htmlFor={`${id}-reason`}>Reason <span className="muted">(required, logged)</span></label>
        <input id={`${id}-reason`} name="reason" type="text" required maxLength={2000} autoComplete="off" />
      </fieldset>
      {state.error ? <p className="callout callout-warn" role="alert">{state.error}</p> : null}
      {state.done ? <p role="status">{state.done}</p> : null}
    </form>
  );
}

/** Reset, chaos and tamper for an admin (spec 0095, A19). */
export function DemoControls({ path, clerks, chaos }: { path: string; clerks: { id: string; name: string }[]; chaos: boolean }) {
  return (
    <>
      <Control action={resetAction} path={path} title="Reset the demo"
               hint="A fresh demo company. With a clerk chosen, the Trust Ledger and Acme's change-request email load too. The team phone is set only from the shell, so calls stay off until then.">
        <label htmlFor="reset-clerk">Clerk to link</label>
        <select id="reset-clerk" name="link_clerk_user_id" defaultValue="">
          <option value="">Nobody (each release then needs two approvers)</option>
          {clerks.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <div className="actions"><button type="submit" className="primary">Reset</button></div>
      </Control>
      <Control action={chaosAction} path={path} title="Chaos mode" hint="Ramp starts failing holds and timing out, to show the payment stays stopped.">
        <div className="actions">
          {chaos ? <button type="submit" name="enabled" value="false">Turn chaos off</button>
                 : <button type="submit" name="enabled" value="true" className="danger">Turn chaos on</button>}
        </div>
      </Control>
      <Control action={tamperAction} path={path} title="Tamper with the evidence chain"
               hint="Rewrites one row of this demo's chain, so the chain check catches it. It can't be undone.">
        <label htmlFor="tamper-confirm">Type <strong>break the chain</strong> to confirm</label>
        <input id="tamper-confirm" name="confirm" type="text" required autoComplete="off" />
        <div className="actions"><button type="submit" className="danger">Tamper</button></div>
      </Control>
    </>
  );
}
