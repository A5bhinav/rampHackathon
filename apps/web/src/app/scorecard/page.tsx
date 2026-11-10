import type { Metadata } from "next";
import Link from "next/link";
import { ScorecardEmpty } from "@/components/scorecard";
import { PublicHeader } from "@/components/public-header";
import { RingBand } from "@/components/ring-band";

export const metadata: Metadata = { title: "Dialback: scorecard" };

// The scorecard (spec 0039). The attack lab (evals/) hasn't produced one, so this page has no
// numbers: it says what the scorecard will show and links to what does exist.
export default function ScorecardPage() {
  return (
    <div className="public">
      <PublicHeader links={[{ href: "/", label: "Front door" }, { href: "/corpus", label: "Frozen test cases" }]} />
      <main>
        <RingBand
          eyebrow="Scorecard"
          titleId="sc-h"
          title="The attack lab isn't built yet, so there is no scorecard."
          lead={<>
            The lab would replay hundreds of scams and legitimate-but-odd changes through the real pipeline, on a simulated clock,
            and score how often Dialback is fooled or wrongly blocks a real vendor. Until it runs, this page shows no numbers: we
            don&apos;t display a result the system didn&apos;t compute.
          </>}
        />
        <ScorecardEmpty />
        <section className="two-up">
          <div className="card">
            <div className="card-head"><h2>What the scorecard will show</h2></div>
            <ul className="card-body plain">
              <li>False Verified: attacks that ended Verified, with the upper bound of its rate, next to how many seed families the runs came from.</li>
              <li>False holds: legitimate changes that didn&apos;t end Verified.</li>
              <li>Results by attack class, and the hard gates a change to the agent, ledger, rules or prompts must pass.</li>
            </ul>
          </div>
          <div className="card">
            <div className="card-head"><h2>What exists today</h2></div>
            <ul className="card-body plain">
              <li><Link href="/corpus">The number-picking rule against 20 frozen cases</Link>, run by the real rule.</li>
              <li>The rule table, the state machine and the evidence chain, each with its own tests in the repo.</li>
            </ul>
          </div>
        </section>
      </main>
    </div>
  );
}
