"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { TOUR, tourStep } from "@/lib/tour";

// The guided demo's panel (specs 0039, 0041): a floating card on any page opened with ?tour=N. Links only.
export function TourPanel() {
  const step = tourStep(useSearchParams().get("tour") ?? "");
  const path = usePathname();
  if (!step) return null;
  return (
    <aside className="tour" aria-label="Guided demo">
      <p className="tour-count"><span>Guided demo</span><span>{step.n} of {TOUR.length}</span></p>
      <ol className="tour-bar" aria-hidden="true">
        {TOUR.map((t) => <li key={t.n} className={t.n <= step.n ? "is-on" : ""} />)}
      </ol>
      <h2 className="tour-title">{step.title}</h2>
      <p className="tour-caption">{step.caption}</p>
      <div className="tour-nav">
        {step.n > 1 ? <Link className="button-link" href={`/tour/${step.n - 1}`}>Back</Link> : null}
        {step.n < TOUR.length ? <Link className="button-link primary" href={`/tour/${step.n + 1}`}>Next: {TOUR[step.n].title}</Link> : null}
        <Link className="tour-end" href={path}>End the tour</Link>
      </div>
    </aside>
  );
}
