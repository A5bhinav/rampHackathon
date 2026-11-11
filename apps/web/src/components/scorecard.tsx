import { Icon } from "./icons";

// The scorecard's empty state (specs 0039, 0063): the attack lab hasn't produced a run on this
// commit, so there are no numbers to show, and none are made up.
export function ScorecardEmpty() {
  return (
    <section className="card empty-state" aria-label="No lab run">
      <span className="empty-icon"><Icon name="clock" size={28} /></span>
      <p className="empty-title">No lab run on this commit yet</p>
      <p className="muted sub">Results appear here only from a lab run&apos;s own output, never from sample numbers.</p>
    </section>
  );
}
