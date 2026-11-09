import type { CallAttempt, CallScript, DecisionInfo } from "@/lib/api-types";
import { SLOT_LABEL, answerText, checkRows, conversation, ruleChips } from "@/lib/call";
import { when, words } from "@/lib/format";
import { Icon } from "./icons";

// One call as a conversation (spec 0036): the fixed disclosure, each scripted question, what the
// callee said as code recorded it, and the rules each answer fed. Nothing here is decided: the
// chips carry the core's status words, and the last four digits were never sent to the page.
export function CallCard({ call, script, decision, total }: {
  call: CallAttempt; script: CallScript | undefined; decision: DecisionInfo | null; total: number;
}) {
  const talked = call.answers.length > 0;
  const turns = conversation(script, call.answers);
  const checks = checkRows(call.checks);
  return (
    <section className="card call-card" aria-labelledby={`call-${call.call_id}`}>
      <div className="call-head">
        <h2 id={`call-${call.call_id}`}><Icon name="call" /> The call{total > 1 ? `, attempt ${call.attempt}` : ""}</h2>
        <span className={`badge ${call.result === "answers_recorded" ? "tone-ok" : call.result ? "tone-warn" : "tone-info"}`}>
          {call.result ? words(call.result) : "in progress"}
        </span>
      </div>
      <dl className="call-meta">
        <div><dt>Started</dt><dd>{when(call.started_at) || "—"}</dd></div>
        <div><dt>Answered by</dt><dd>{call.answered_by === "human" ? "a person" : call.answered_by === "machine" ? "a machine" : "unknown"}</dd></div>
        <div><dt>Dialed</dt><dd>{call.dialed_number_is_pick === true ? "the Trust Ledger's pick" : call.dialed_number_is_pick === false ? <span className="badge tone-bad">not the Trust Ledger&apos;s pick</span> : "unknown"}{call.direction ? `, ${call.direction}` : ""}</dd></div>
        {call.provider ? <div><dt>Line</dt><dd>{call.provider}</dd></div> : null}
        {call.agent_version ? <div><dt>Agent</dt><dd className="mono">{call.agent_version}</dd></div> : null}
      </dl>
      {call.reason ? <p className="muted">{call.reason}</p> : null}

      {talked ? (
        <>
        <p className="muted sub">
          Each answer shows the turn it came from and chips for the decision rules that read it, with the status the decision
          recorded for that rule. A rule that reads several answers shows under each of them.
        </p>
        <ol className="convo" aria-label="The conversation, as recorded">
          {script ? (
            <li className="say say-agent">
              <span className="say-who">Agent · the fixed disclosure (today&apos;s script) · disclosed first: {call.checks ? (call.checks.disclosure_first ? "yes" : "no") : "not reported"}</span>
              <p>{script.disclosure}</p>
            </li>
          ) : null}
          {turns.asked.map((q) => (
            <li key={q.slot} className="say-pair">
              <div className="say say-agent">
                <span className="say-who">Agent · {SLOT_LABEL[q.slot] ?? q.slot} · scripted wording</span>
                <p>{q.wording}</p>
              </div>
              {q.answers.length ? q.answers.map((x, i) => <Answer key={`${x.slot}-${x.turn_id}-${i}`} answer={x} decision={decision} />) : (
                <div className="say say-callee is-none"><p>No answer recorded.</p></div>
              )}
            </li>
          ))}
          {turns.extra.map((x, i) => <li key={`${x.slot}-${x.turn_id}-${i}`}><Answer answer={x} decision={decision} /></li>)}
        </ol>
        </>
      ) : null}

      {checks.length ? (
        <>
          <h3 className="panel-sub">Script checks, computed by code from the call</h3>
          <ul className="checks">
            {checks.map((r) => (
              <li key={r.label} className={r.ok ? "is-ok" : "is-bad"}>
                <span>{r.label}</span><span className={`badge ${r.ok ? "tone-ok" : "tone-bad"}`}>{r.word}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {script && talked ? <p className="muted sub call-note">{script.note}</p> : null}
    </section>
  );
}

function Answer({ answer, decision }: { answer: CallAttempt["answers"][number]; decision: DecisionInfo | null }) {
  const chips = ruleChips(answer.feeds, decision);
  return (
    <div className="say say-callee">
      <span className="say-who">
        Callee · {SLOT_LABEL[answer.slot] ?? answer.slot} · turn {answer.turn_id} · {answer.capture_method === "keypad" ? "keypad" : "spoken"}
      </span>
      <p>{answerText(answer)}</p>
      {chips.length ? (
        <span className="chips" aria-label="Rules this answer fed">
          {chips.map((c) => (
            <a key={c.id} className={`rule-chip tone-${c.tone}`} href={`#rule-${c.id}`} title={c.id}>{c.label} {c.status}</a>
          ))}
        </span>
      ) : null}
    </div>
  );
}
