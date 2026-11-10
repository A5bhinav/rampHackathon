import Link from "next/link";
import type { CallAttempt, CallScript, DecisionInfo } from "@/lib/api-types";
import { SLOT_LABEL, answerText, checkRows, conversation, ruleChips } from "@/lib/call";
import { when, words } from "@/lib/format";

// One call as a conversation (specs 0036, 0041): the fixed disclosure, each scripted question,
// what the callee said as code recorded it, and the rules each answer fed. Nothing here is
// decided: the chips carry the core's status words, and the last four digits never reach the page.
export function CallCard({ call, script, decision, total, watchHref }: {
  call: CallAttempt; script: CallScript | undefined; decision: DecisionInfo | null; total: number; watchHref?: string;
}) {
  const talked = call.answers.length > 0;
  const turns = conversation(script, call.answers);
  const checks = checkRows(call.checks);
  return (
    <section className="card call-card" aria-labelledby={`call-${call.call_id}`}>
      <div className="card-head">
        <span className="section-marker">04</span>
        <h2 id={`call-${call.call_id}`}>Call</h2>
        <span className="muted sub">attempt {call.attempt} of {Math.max(total, call.attempt)}</span>
        <span className={`badge ${call.result === "answers_recorded" ? "tone-ok" : call.result ? "tone-warn" : "tone-info"}`}>
          {call.result ? words(call.result) : "in progress"}
        </span>
        <span className="card-meta">
          <span className="badge tone-muted">AI agent: collects answers only</span>
          {watchHref ? <Link className="button-link" href={watchHref}>Watch the call</Link> : null}
        </span>
      </div>
      <div className="card-body">
        <dl className="call-meta">
          <div><dt>Started</dt><dd>{when(call.started_at) || "—"}</dd></div>
          <div><dt>Answered by</dt><dd>{call.answered_by === "human" ? "a person" : call.answered_by === "machine" ? "a machine" : "unknown"}</dd></div>
          <div><dt>Dialed</dt><dd>{call.dialed_number_is_pick === true ? "the Trust Ledger's pick" : call.dialed_number_is_pick === false ? <span className="badge tone-bad">not the Trust Ledger&apos;s pick</span> : "unknown"}{call.direction ? `, ${call.direction}` : ""}</dd></div>
          {call.provider ? <div><dt>Line</dt><dd>{call.provider}</dd></div> : null}
          {call.agent_version ? <div><dt>Agent</dt><dd className="mono">{call.agent_version}</dd></div> : null}
        </dl>
        {call.reason ? <p className="muted">{call.reason}</p> : null}

        {talked && script ? (
          <div className="disclosure">
            <span className="disclosure-label">Turn 1 · disclosure · disclosed first: {call.checks ? (call.checks.disclosure_first ? "yes" : "no") : "not reported"}</span>
            <p>{script.disclosure}</p>
          </div>
        ) : null}
        {talked ? (
          <ol className="convo" aria-label="The conversation, as recorded">
            <li className="qa qa-head" aria-hidden="true"><span>Scripted question</span><span>Answer</span><span>Turn</span></li>
            {turns.asked.map((q) => (
              <li key={q.slot} className="qa">
                <span className="qa-q"><span className="sr-only">{SLOT_LABEL[q.slot] ?? q.slot}: </span>{q.wording}</span>
                {q.answers.length ? q.answers.map((x, i) => <Answer key={`${x.slot}-${x.turn_id}-${i}`} answer={x} decision={decision} />) : (
                  <span className="qa-a is-none">No answer recorded.</span>
                )}
              </li>
            ))}
            {turns.extra.map((x, i) => (
              <li key={`${x.slot}-${x.turn_id}-${i}`} className="qa">
                <span className="qa-q muted">{SLOT_LABEL[x.slot] ?? x.slot}</span>
                <Answer answer={x} decision={decision} />
              </li>
            ))}
          </ol>
        ) : null}

        {checks.length ? (
          <>
            <h3 className="sr-only">Script checks, computed by code from the call</h3>
            <ul className="checks" aria-label="Script checks, computed by code from the call">
              {checks.map((r) => (
                <li key={r.label} className={r.ok ? "is-ok" : "is-bad"}>
                  <span aria-hidden="true">{r.ok ? "✓" : "✕"}</span> {r.label}: <span className="check-word">{r.word}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {script && talked ? <p className="muted sub call-note">{script.note}</p> : null}
      </div>
    </section>
  );
}

function Answer({ answer, decision }: { answer: CallAttempt["answers"][number]; decision: DecisionInfo | null }) {
  const chips = ruleChips(answer.feeds, decision);
  return (
    <>
      <span className="qa-a">
        <span className="qa-text">{answerText(answer)}</span>
        {chips.length ? (
          <span className="chips" aria-label="Rules this answer fed">
            {chips.map((c) => (
              <a key={c.id} className={`rule-chip tone-${c.tone}`} href={`#rule-${c.id}`} title={c.id}>{c.label} {c.status}</a>
            ))}
          </span>
        ) : null}
      </span>
      <span className="qa-t mono">turn {answer.turn_id} · {answer.capture_method === "keypad" ? "keypad" : "spoken"}</span>
    </>
  );
}
