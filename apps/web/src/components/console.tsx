"use client";

import { useActionState } from "react";
import type { ActionState } from "@/app/d/[datasetId]/(ap)/cases/[caseId]/actions";

type Props = {
  title: string;
  hint: string;
  caseId: string;
  version: number;
  path: string;
  action: (state: ActionState, formData: FormData) => Promise<ActionState>;
  buttons: { value: string; label: string; tone?: "primary" | "danger" }[];
  summary?: React.ReactNode;
  children?: React.ReactNode;
};

/** The release console and the review console (spec 0041 action panel): a required written
 *  reason, and the case version the person is looking at, so a change in between is refused, not
 *  overwritten. A refusal shows the core's own message, with a way to reload. */
export function DecisionConsole({ title, hint, caseId, version, path, action, buttons, summary, children }: Props) {
  const [state, submit, pending] = useActionState(action, {});
  const id = `${title.replace(/\W+/g, "-").toLowerCase()}-reason`;
  return (
    <form className="console" action={submit}>
      <div className="console-head">
        <h2>{title}</h2>
        {summary}
      </div>
      <div className="console-body">
        {children}
        {state.error ? (
          <div className="callout callout-warn" role="alert">
            <div className="callout-body">
              <p className="callout-title">Nothing was changed</p>
              <p>{state.error}</p>
              <p><a className="button-link" href={path}>Reload the case</a></p>
            </div>
          </div>
        ) : null}
        <input type="hidden" name="case_id" value={caseId} />
        <input type="hidden" name="expected_version" value={version} />
        <input type="hidden" name="path" value={path} />
        <label htmlFor={id}>Reason <span className="muted">(required)</span></label>
        <textarea id={id} name="reason" rows={3} required maxLength={2000} />
        <div className="actions">
          {buttons.map((b) => (
            <button key={b.value} type="submit" name="action" value={b.value} className={b.tone ?? ""} disabled={pending}>{b.label}</button>
          ))}
        </div>
        <p className="muted console-hint">{hint}</p>
      </div>
    </form>
  );
}
