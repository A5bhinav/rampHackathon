import type { Metadata } from "next";
import Link from "next/link";
import { BrandMark } from "@/components/nav";
import { CHANNEL, CORPUS, REASON_SHORT, groups, numberVerdict, pickedPlanted, type CorpusCase } from "@/lib/corpus";
import { phone } from "@/lib/lineage";

export const metadata: Metadata = { title: "Dialback: the number-picking rule against planted attacks" };

// The corpus page (spec 0038). Static: every verdict here was written by scripts/corpus/results.py
// running the real rule; the page only lays it out.
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "—");

export default function CorpusPage() {
  const all = CORPUS.cases;
  const matched = all.filter((c) => c.matches).length;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <nav className="nav" aria-label="Main">
            <Link className="brand" href="/"><BrandMark />Dialback</Link>
            <Link href="/">How it works</Link>
          </nav>
          <div className="who"><Link className="button-link primary" href="/app">Open the dashboard</Link></div>
        </div>
      </header>
      <main className="shell story">
        <section className="story-hero" aria-labelledby="corpus-h">
          <p className="eyebrow">The number-picking rule against {CORPUS.cases.length} frozen test cases</p>
          <h1 id="corpus-h">Which number would Dialback call?</h1>
          <p className="lead">
            Before any call, the Trust Ledger picks a number it can prove is the vendor&apos;s (R2). These are the frozen test cases
            for that one step: a fraudster plants a number in an email thread, a lookalike domain, hidden PDF text, a fake listing,
            and the rule must not pick it. Each case&apos;s expected result is frozen and its hash pinned in the repo, so it can&apos;t drift to match the code without a reviewed change.
          </p>
          <p className="note">
            This is the selection rule only, run by <span className="mono">{CORPUS.generated_by}</span> with{" "}
            <span className="mono">{CORPUS.rule_version}</span>. It is not the attack lab, which would replay whole scams
            through calls and decisions; the lab isn&apos;t built, so there is no scorecard.
          </p>
        </section>

        <dl className="stats" aria-label="Results">
          <div className="stat"><dt>Cases</dt><dd>{all.length}</dd></div>
          <div className="stat"><dt>Rule did what the frozen truth says</dt><dd>{matched} of {all.length}</dd></div>
          <div className="stat is-bad"><dt>Rule picked a planted number</dt><dd>{all.filter(pickedPlanted).length}</dd></div>
        </dl>

        {groups(all).map((g) => (
          <section key={g.kind} className="story-section" aria-labelledby={`g-${g.kind}`}>
            <h2 id={`g-${g.kind}`}>{g.title} <span className="count">{g.cases.length}</span></h2>
            <p className="muted story-intro">{g.lead}</p>
            {g.cases.map((c) => <Case key={c.id} c={c} />)}
          </section>
        ))}
      </main>
    </>
  );
}

function Case({ c }: { c: CorpusCase }) {
  const planted = pickedPlanted(c);
  const limit = c.kind === "known_limit";
  return (
    <article className={`card corpus-case ${planted ? "edge-bad lead-card" : ""}`} id={c.id} aria-labelledby={`${c.id}-h`}>
      <div className="corpus-head">
        <h3 id={`${c.id}-h`}><span className="mono muted">{c.id}</span> {c.title}</h3>
        <span className="tags">
          {c.source === "proposed" ? <span className="badge tone-muted" title="Spec 0020, Q42">proposed, awaiting team sign-off</span> : null}
          <span className={`badge ${c.matches ? "tone-ok" : "tone-bad"}`}>{c.matches ? "matches the truth" : "differs from the truth"}</span>
        </span>
      </div>
      <p className="muted sub">
        {c.vendor} · change request &ldquo;{c.request.subject}&rdquo; · expected:{" "}
        {c.expected.pick ? <span className="mono">{phone(c.expected.pick)}</span> : "no number (a person reviews)"} · the rule:{" "}
        {c.actual.pick ? <span className="mono">{phone(c.actual.pick)}</span> : "no provable number"}
      </p>
      {planted && limit ? (
        <p className="result error">
          The rule picks the number the attacker planted, as the design says it will. The attacker has controlled the vendor&apos;s
          mailbox and a public listing for months, which this rule counts as two independent sources; the vendor&apos;s real numbers
          were only ever seen by email, which counts as one. With no stored contract to contradict them, the attacker answers the
          call too, so the call isn&apos;t a backstop here. The second approver, reading the evidence, is.
        </p>
      ) : planted ? <p className="result error">The rule picked a number the attacker planted.</p> : null}
      {c.problems.length ? <p className="result error">{c.problems.join("; ")}</p> : null}
      <div className="table-wrap">
        <table className="corpus-table">
          <thead><tr><th>Number</th><th>Whose</th><th>The rule</th><th>Why</th><th className="num">Seen</th><th>Where</th><th>First seen</th></tr></thead>
          <tbody>
            {c.planted.map((p) => {
              const v = numberVerdict(p, c.actual.pick);
              return (
                <tr key={p.e164} className={p.truth === "poison" ? "is-poison" : ""}>
                  <td className="mono nowrap">{phone(p.e164)}</td>
                  <td>{p.truth === "poison" ? <span className="badge tone-bad">planted by the attacker</span> : <span className="badge tone-muted">the vendor&apos;s</span>}</td>
                  <td><span className={`badge tone-${v.tone}`}>{v.word}</span></td>
                  <td>
                    {p.reasons.length ? (
                      <ul className="reason-chips">
                        {p.reasons.map((r) => <li key={r} title={CORPUS.reason_text[r] ?? r}>{REASON_SHORT[r] ?? r.replaceAll("_", " ")}</li>)}
                      </ul>
                    ) : <span className="muted">—</span>}
                  </td>
                  <td className="num">{p.sightings}</td>
                  <td>{p.channels.map((ch) => CHANNEL[ch] ?? ch).join(", ")}{p.hidden !== "none" ? <div className="cell-note muted sub">{p.hidden === "all" ? "only in hidden text" : "partly in hidden text"}</div> : null}</td>
                  <td className="nowrap">{day(p.first_seen_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted sub">The change request arrived {day(c.request.received_at)}. &ldquo;First seen&rdquo; is the rule&apos;s own reading. Hover a reason for the ledger&apos;s own sentence.</p>
    </article>
  );
}
