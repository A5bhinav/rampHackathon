"use client";

import { useActionState } from "react";
import { accessAction, rampLinkAction, roleAction, type PeopleState } from "@/app/d/[datasetId]/control/people/actions";
import type { PersonAdminView } from "@/lib/api-types";
import { ROLES, ROLE_LABEL } from "@/lib/people";

type Action = (state: PeopleState, form: FormData) => Promise<PeopleState>;

function Change({ action, person, path, label, children }: {
  action: Action; person: PersonAdminView; path: string; label: string; children: React.ReactNode;
}) {
  const [state, submit, pending] = useActionState(action, {});
  const id = `${label.replace(/\W+/g, "-").toLowerCase()}-${person.id}`;
  return (
    <form className="admin-change" action={submit}>
      <input type="hidden" name="user_id" value={person.id} />
      <input type="hidden" name="path" value={path} />
      <fieldset disabled={pending}>
        <legend>{label}</legend>
        {children}
        <label htmlFor={`${id}-reason`}>Reason <span className="muted">(required, logged)</span></label>
        <input id={`${id}-reason`} name="reason" type="text" required maxLength={2000} autoComplete="off" />
      </fieldset>
      {state.error ? <p className="callout callout-warn" role="alert">{state.error}</p> : null}
      {state.done ? <p className="muted" role="status">{state.done}</p> : null}
    </form>
  );
}

/** One person's controls for an admin (spec 0095, A17). Your own Ramp link and access aren't offered:
 *  the database refuses both (D5, D7). */
export function PersonControls({ person, self, path }: { person: PersonAdminView; self: boolean; path: string }) {
  return (
    <details className="admin-controls">
      <summary>Manage</summary>
      <Change action={roleAction} person={person} path={path} label="Role">
        <select name="role" aria-label="Role" defaultValue="approver">
          {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
        </select>
        <div className="actions">
          <button type="submit" name="grant" value="true">Grant</button>
          <button type="submit" name="grant" value="false" className="danger">Take away</button>
        </div>
      </Change>
      {self ? null : (
        <Change action={rampLinkAction} person={person} path={path} label="Ramp user">
          <input name="ramp_user_id" type="text" aria-label="Ramp user id" defaultValue={person.ramp_user_id ?? ""}
                 placeholder="Ramp user id; empty clears the link" maxLength={200} autoComplete="off" />
          <p className="muted sub">You become a changer of this person's Ramp changes, so you can't release them (R5).</p>
          <div className="actions"><button type="submit">Save link</button></div>
        </Change>
      )}
      {self ? null : (
        <Change action={accessAction} person={person} path={path} label="Access">
          <div className="actions">
            {person.access_enabled
              ? <button type="submit" name="enabled" value="false" className="danger">Turn off access</button>
              : <button type="submit" name="enabled" value="true">Turn access back on</button>}
          </div>
        </Change>
      )}
    </details>
  );
}
