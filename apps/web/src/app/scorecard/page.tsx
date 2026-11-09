import type { Metadata } from "next";
import Link from "next/link";
import { BrandMark } from "@/components/nav";

export const metadata: Metadata = { title: "Dialback: scorecard" };

// The scorecard (spec 0039). The attack lab (evals/) hasn't produced one, so this page has no
// numbers: it says what the scorecard will show and links to what does exist.
export default function ScorecardPage() {
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <nav className="nav" aria-label="Main">
            <Link className="brand" href="/"><BrandMark />Dialback</Link>
            <Link href="/">How it works</Link>
            <Link href="/corpus">Frozen test cases</Link>
          </nav>
          <div className="who"><Link className="button-link primary" href="/app">Open the dashboard</Link></div>
        </div>
      </header>
      <main className="shell story">
        <section className="story-hero" aria-labelledby="sc-h">
          <p className="eyebrow">Scorecard</p>
          <h1 id="sc-h">The attack lab isn&apos;t built yet, so there is no scorecard.</h1>
          <p className="lead">
            The lab would replay hundreds of scams and legitimate-but-odd changes through the real pipeline, on a simulated clock,
            and score how often Dialback is fooled or wrongly blocks a real vendor. Until it runs, this page shows no numbers: we
            don&apos;t display a result the system didn&apos;t compute.
          </p>
        </section>
        <section className="story-section real">
          <div className="panel">
            <h3>What the scorecard will show</h3>
            <ul>
              <li>False Verified: attacks that ended Verified, with the upper bound of its rate, next to how many seed families the runs came from.</li>
              <li>False holds: legitimate changes that didn&apos;t end Verified.</li>
              <li>Results by attack class, and the hard gates a change to the agent, ledger, rules or prompts must pass.</li>
            </ul>
          </div>
          <div className="panel">
            <h3>What exists today</h3>
            <ul>
              <li><Link href="/corpus">The number-picking rule against 20 frozen cases</Link>, run by the real rule.</li>
              <li>The rule table, the state machine and the evidence chain, each with its own tests in the repo.</li>
            </ul>
          </div>
        </section>
      </main>
    </>
  );
}
