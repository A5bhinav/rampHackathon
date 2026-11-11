"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { TOUR, activeStep, tourAction } from "@/lib/tour";

const KEY = "dialback-tour";
const store = {  // per-tab convenience only; any storage error means no remembered beat
  get: () => { try { return window.sessionStorage.getItem(KEY); } catch { return null; } },
  set: (v: string | null) => { try { if (v) window.sessionStorage.setItem(KEY, v); else window.sessionStorage.removeItem(KEY); } catch { /* none */ } },
};

// The guided demo's panel (specs 0039, 0041, 0066): a floating card on any page opened with ?tour=N.
// Links only. A beat with an action carries one button for it; when that button is the next beat, it
// takes the place of Next.
export function TourPanel() {
  const fromUrl = useSearchParams().get("tour");
  const path = usePathname();
  const [stored, setStored] = useState<string | null>(null);  // read after mount: no server/browser mismatch
  useEffect(() => {
    const valid = activeStep(fromUrl, null);
    if (valid) store.set(String(valid.n));
    setStored(store.get());
  }, [fromUrl, path]);
  const step = activeStep(fromUrl, stored);
  if (!step) return null;
  const action = tourAction(step);
  return (
    <aside className="tour" aria-label={`Step ${step.n} of ${TOUR.length}`}>
      <p className="tour-count">Step {step.n} of {TOUR.length}</p>
      <ol className="tour-bar" aria-hidden="true">
        {TOUR.map((t) => <li key={t.n} className={t.n <= step.n ? "is-on" : ""} />)}
      </ol>
      <h2 className="tour-title">{step.title}</h2>
      <p className="tour-caption">{step.caption}</p>
      <div className="tour-nav">
        {step.n > 1 ? <Link className="button-link" href={`/tour/${step.n - 1}`}>Back</Link> : null}
        {action && action !== `/tour/${step.n + 1}` ? <Link className="button-link primary" href={action}>{step.action!.label}</Link> : null}
        {step.n < TOUR.length ? (
          <Link className={`button-link${action && action !== `/tour/${step.n + 1}` ? "" : " primary"}`} href={`/tour/${step.n + 1}`}>
            {action === `/tour/${step.n + 1}` ? step.action!.label : `Next: ${TOUR[step.n].title}`}
          </Link>
        ) : null}
        <Link className="tour-end" href={path} onClick={() => { store.set(null); setStored(null); }}>End the tour</Link>
      </div>
    </aside>
  );
}
