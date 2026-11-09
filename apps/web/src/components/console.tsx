"use client";

import { useActionState } from "react";
import type { ActionState } from "@/app/d/[datasetId]/cases/[caseId]/actions";

type Props = {
  title: string;
  hint: string;
  caseId: string;
  version: number;
  path: string;
  action: (state: ActionState, formData: FormData) => Promise<ActionState>;
  buttons: { value: string; label: string; tone?: "primary" | "danger" }[];
};

/** The release console and the review console: a required written reason, and the case
 *  version the person is looking at, so a change in between is refused, not overwritten. */
export function DecisionConsole({ title, hint, caseId, version, path, action, buttons }: Props) {
  const [state, submit, pending] = useActionState(action, {});
  return (
    <form className="console" action={submit}>
      <h2>{title}</h2>
      <p className="muted console-hint">{hint}</p>
      <input type="hidden" name="case_id" value={caseId} />
      <input type="hidden" name="expected_version" value={version} />
      <input type="hidden" name="path" value={path} />
      <label htmlFor={`${title}-reason`}>Reason (required, kept in the evidence log)</label>
      <textarea id={`${title}-reason`} name="reason" required maxLength={2000} />
      <div className="actions">
        {buttons.map((b) => (
          <button key={b.value} type="submit" name="action" value={b.value} className={b.tone ?? ""} disabled={pending}>{b.label}</button>
        ))}
      </div>
      {state.error ? <p className="result error" role="alert">{state.error}</p> : null}
    </form>
  );
}
