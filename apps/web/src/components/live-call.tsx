"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { OutcomeBadge } from "./badges";
import { CaseRail } from "./flow";
import { BrandMark } from "./nav";
import { Rings } from "./ring-band";
import type { LiveCallView } from "@/lib/live-call";
import { refreshDelay } from "@/lib/stations";

// The live call (spec 0065, plan v3 section 4.1): a phone frame on the left, the call's answers on
// the right, the seven stations on top. It asks the dashboard's own route while code is working
// (every 2 seconds for a minute, then every 10), one request at a time with a 5-second timeout, and
// stops when the case settles, the session ends or the case is gone. The browser never calls the core.
export function LiveCall({ initial, stateUrl, caseHref, flowHref }: { initial: LiveCallView; stateUrl: string; caseHref: string; flowHref: string }) {
  const [view, setView] = useState(initial);
  const now = useNow(view.phase === "on_call");

  const [started] = useState(() => Date.now());
  const [ended, setEnded] = useState(false);  // signed out or the case is gone: stop asking
  useEffect(() => {
    if (!view.poll || ended) return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      if (stop) return;
      if (document.visibilityState === "visible") {
        try {
          // Ask for the number too until we have one; it is read on the server, never in the browser.
          const res = await fetch(view.dialed ? stateUrl : `${stateUrl}?selection=1`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
          if (res.status === 401 || res.status === 404) { if (!stop) setEnded(true); return; }
          if (res.ok) {
            const next = (await res.json()) as LiveCallView;
            // Keep the number only within the same bank change; a new change chooses again.
            if (!stop) setView((prev) => ({ ...next, dialed: next.dialed ?? (next.version === prev.version ? prev.dialed : null) }));
            return;  // the new view re-arms this effect
          }
        } catch { /* a timeout or a blip: the next tick tries again */ }
      }
      if (!stop) timer = window.setTimeout(tick, refreshDelay(Date.now() - started));
    };
    timer = window.setTimeout(tick, refreshDelay(Date.now() - started));
    return () => { stop = true; window.clearTimeout(timer); };
  }, [view, stateUrl, started, ended]);

  // A running timer only after mount (the server and the browser read different clocks); an
  // ended call's length is the same everywhere.
  const end = view.endedAt ? Date.parse(view.endedAt) : now;
  const elapsed = view.startedAt && end !== null ? clock(end - Date.parse(view.startedAt)) : null;
  const status = view.phase === "dialing" && view.dialed ? `Dialing ${view.dialed}` : view.status;
  const tone = view.phase === "decided" ? (view.outcome === "verified" ? "ok" : view.outcome === "not_verified" ? "bad" : "warn")
    : view.phase === "not_connected" ? "warn" : "info";
  const asking = view.phase === "on_call" ? view.disclosure
    : [...view.slots].reverse().find((s) => s.answer)?.wording ?? (view.phase === "dialing" ? view.disclosure : null);
  return (
    <main className="live">
      <header className="live-top">
        <Link className="brand" href={caseHref}><BrandMark />Dialback</Link>
        <span className="live-vendor">{view.vendor}</span>
        <span className={`badge tone-${tone}`}>{view.stateLabel}</span>
        {view.attempt ? <span className="muted sub">attempt {view.attempt}</span> : null}
        {elapsed ? <span className="mono live-timer" role="timer" aria-label={`Call time ${elapsed}`}>{elapsed}</span> : null}
        <span className="live-links"><Link href={caseHref}>Case</Link><Link href={flowHref}>Flow mode</Link></span>
      </header>
      <div className="live-rail"><CaseRail steps={view.steps} /></div>
      <div className="live-split">
        <section className="phone-wrap" aria-label="The call">
          <Rings className="rings phone-rings" />
          <div className="phone">
            <p className="phone-status" aria-live="polite">{status}{elapsed && view.phase === "on_call" ? ` · ${elapsed}` : ""}</p>
            {view.dialed ? <p className="phone-number mono">{view.dialed}</p> : null}
            <p className="phone-who">{view.vendor}</p>
            {asking ? <p className="phone-bubble">{asking}</p> : null}
            {view.phase === "not_connected" && view.reason ? <p className="phone-note">{view.reason}</p> : null}
            <span className="phone-pad" aria-hidden="true">{Array.from({ length: 9 }, (_, i) => <span key={i} />)}</span>
          </div>
        </section>
        <section className="card live-answers" aria-labelledby="answers-h">
          <div className="card-head">
            <span className="section-marker">Call</span>
            <h2 id="answers-h">Call answers</h2>
            <span className="card-meta"><span className="badge tone-muted">AI agent: collects answers only</span></span>
          </div>
          <div className="card-body">
            {view.disclosure ? <div className="disclosure"><span className="disclosure-label">Disclosure</span><p>{view.disclosure}</p></div> : null}
            {view.waiting ? <p className="live-waiting" role="status">{view.waiting}</p> : null}
            {view.phase === "no_call" && view.lastStep ? <p className="quiet-fill">{view.lastStep}</p> : null}
            <ol className="slots" aria-label="Scripted questions and recorded answers">
              {view.slots.map((s) => (
                <li key={s.slot} className={s.answer ? "is-filled" : "is-empty"} style={s.order !== null ? { animationDelay: `${s.order * 120}ms` } : undefined}>
                  <span className="slot-q">{s.wording}</span>
                  <span className="slot-a">{s.answer ?? <><span aria-hidden="true">—</span><span className="sr-only">no answer yet</span></>}</span>
                  <span className="slot-t mono">{s.turn !== null ? `turn ${s.turn} · ${s.method === "keypad" ? "keypad" : "spoken"}` : ""}</span>
                </li>
              ))}
            </ol>
            {view.checks.length ? (
              <ul className="checks" aria-label="Script checks, computed by code from the call">
                {view.checks.map((r) => <li key={r.label} className={r.ok ? "is-ok" : "is-bad"}><span aria-hidden="true">{r.ok ? "✓" : "✕"}</span> {r.label}: <span className="check-word">{r.word}</span></li>)}
              </ul>
            ) : null}
            {view.phase === "decided" ? (
              <div className="live-decided">
                <OutcomeBadge outcome={view.outcome} />
                {view.effect ? <p>{view.effect}</p> : null}
                <Link href={`${caseHref}#decision-h`}>See the decision</Link>
              </div>
            ) : null}
            {view.note && view.slots.some((s) => s.answer) ? <p className="muted sub">{view.note}</p> : null}
          </div>
        </section>
      </div>
    </main>
  );
}

function useNow(ticking: boolean): number | null {
  const [now, setNow] = useState<number | null>(null);  // null until mounted: no server/browser mismatch
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [ticking]);
  return now;
}

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
