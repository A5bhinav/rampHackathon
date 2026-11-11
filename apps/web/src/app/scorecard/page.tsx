import type { Metadata } from "next";
import Link from "next/link";
import { LabResults } from "@/components/scorecard";
import { PublicHeader } from "@/components/public-header";
import { PageHead } from "@/components/page";

export const metadata: Metadata = { title: "Dialback: scorecard" };

// The scorecard (specs 0039, 0101). Static: every result here was written by `lab snapshot` running
// the real attack lab; the page only lays it out.
export default function ScorecardPage() {
  return (
    <div className="public">
      <PublicHeader links={[{ href: "/", label: "Front door" }, { href: "/corpus", label: "Frozen test cases" }]} />
      <main>
        <PageHead titleId="sc-h" title="Scorecard" lead="One real run of the attack lab, failures included." />
        <p className="page-intro muted">The attack lab replays scams and legitimate-but-odd changes through the real pipeline, on a simulated clock,
            and scores how often Dialback is fooled or wrongly blocks a real vendor. Everything below came from one run of the lab, on the
            commit it names: we don&apos;t display a result the system didn&apos;t compute.</p>
        <LabResults />
        <section className="two-up">
          <div className="card">
            <div className="card-head"><h2>Still to come</h2></div>
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
