// Drawings of the dashboard for the public page (spec 0040). Decorative: each sits beside text
// that says the same thing, so screen readers skip it. Content comes from lib/showcase.ts.
import { Icon } from "@/components/icons";
import { HERO_CASES, RAIL, SHOWCASE, type Picture } from "@/lib/showcase";
import type { StoryKey } from "@/lib/story";

function Frame({ title, badge, children }: { title: string; badge?: { text: string; tone: string }; children: React.ReactNode }) {
  return (
    <div className="shot" aria-hidden="true">
      <div className="shot-back" />
      <div className="shot-card">
        <div className="shot-head">
          <span>{title}</span>
          {badge ? <span className={`badge tone-${badge.tone}`}>{badge.text}</span> : null}
        </div>
        {children}
      </div>
    </div>
  );
}

function Body({ p }: { p: Picture }) {
  switch (p.kind) {
    case "change":
      return (
        <Frame title={`${p.vendor} · bank details`} badge={{ text: "changed", tone: "warn" }}>
          <dl className="shot-diff">
            <div><dt>Before</dt><dd className="mono">{p.from}</dd></div>
            <div className="now"><dt>Now</dt><dd className="mono">{p.to}</dd></div>
          </dl>
          <p className="shot-foot"><Icon name="mail" size={14} /><span>{p.subject} · from {p.sender} · entered by {p.by}</span></p>
        </Frame>
      );
    case "detect":
      return (
        <Frame title="Detection" badge={{ text: "agreed", tone: "info" }}>
          <ul className="shot-list">
            {p.signals.map((s) => <li key={s.name}><span>{s.name}</span><span className="muted">{s.does}</span></li>)}
          </ul>
          <p className="shot-result"><Icon name="arrow" size={14} /> {p.result}</p>
        </Frame>
      );
    case "hold":
      return (
        <Frame title={`${p.vendor} · scheduled payments`} badge={{ text: "on hold", tone: "info" }}>
          <ul className="shot-list">
            {p.bills.map((b) => (
              <li key={b.invoice}><span className="mono">{b.invoice}</span><span className="mono">{b.amount}</span>
                <span className="muted">{b.due}</span><Icon name="hold" size={14} /></li>
            ))}
          </ul>
          <p className="shot-result ok">{p.readback}</p>
        </Frame>
      );
    case "number":
      return (
        <Frame title="Trust Ledger · number to call" badge={{ text: "picked", tone: "ok" }}>
          <p className="shot-big mono">{p.picked}</p>
          <p className="shot-chips">{p.sources.map((s) => <span key={s} className="shot-chip">{s}</span>)}</p>
          <ul className="shot-checks">{p.checks.map((c) => <li key={c}>{c}</li>)}</ul>
          <p className="shot-refused"><span className="badge tone-bad">refused</span><span>{p.refused.what}: {p.refused.why}</span></p>
        </Frame>
      );
    case "call":
      return (
        <Frame title="Call 1 · answered by a person" badge={{ text: "completed", tone: "ok" }}>
          <ol className="shot-convo">
            <li className="agent first">{p.disclosure}</li>
            {p.turns.map((t) => (
              <li key={t.slot}><span className="agent">{t.ask}</span><span className="vendor">{t.answer}</span></li>
            ))}
          </ol>
        </Frame>
      );
    case "decide":
      return (
        <Frame title="Decision" badge={{ text: p.outcome, tone: "ok" }}>
          <p className="shot-effect">{p.effect}</p>
          <ul className="shot-checks">
            {p.rows.map((r) => <li key={r.id}><span className="mono">{r.id}</span> {r.sentence}</li>)}
          </ul>
          <p className="shot-foot">{p.more}: passed</p>
        </Frame>
      );
    case "release":
      return (
        <Frame title="Release" badge={{ text: "awaiting approver", tone: "ok" }}>
          <p className="shot-refused"><span className="badge tone-bad">refused</span><span>{p.refusal}</span></p>
          <p className="shot-field"><span className="muted">Reason, written by {p.approver}</span>{p.reason}</p>
          <p className="shot-button"><span>Release payments</span></p>
        </Frame>
      );
  }
}

export function StationShot({ station }: { station: StoryKey }) {
  return <Body p={SHOWCASE[station]} />;
}

/** The hero: the demo company's queue, each case with its rail. */
export function HeroShot() {
  return (
    <div className="shot hero-shot" aria-hidden="true">
      <div className="shot-back" />
      <div className="shot-card">
        <div className="shot-head"><span>Fictional Co (demo) · bank changes</span><span className="badge tone-muted">demo data</span></div>
        <ul className="hero-cases">
          {HERO_CASES.map((c) => (
            <li key={c.vendor}>
              <div className="hero-case-top">
                <strong>{c.vendor}</strong>
                <span className={`badge tone-${c.tone}`}>{c.state}</span>
              </div>
              <p className="muted">Next payment held: <span className="mono">{c.next}</span> · <span className="mono">{c.amount}</span></p>
              <ol className="mini-rail">
                {RAIL.map((r, i) => (
                  <li key={r} className={c.skipped.includes(i) ? "skipped" : i < c.at ? "done" : i === c.at ? `now tone-${c.tone}` : ""}>{r}</li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
