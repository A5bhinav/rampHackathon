import Link from "next/link";
import { CorpusGroups, CorpusStats } from "@/components/corpus";
import { CardHead, PageHead } from "@/components/page";
import { ScorecardEmpty } from "@/components/scorecard";
import { CORPUS } from "@/lib/corpus";

// Assurance (spec 0063): what's been tested, inside Control. The number-picking rule against the
// frozen corpus, and the attack lab's scorecard, which has no run yet and so shows no numbers.
export default function AssurancePage() {
  return (
    <main>
      <PageHead eyebrow="Control · testing" title="Assurance"
        lead={<>The selection rule, <span className="mono">{CORPUS.rule_version}</span>, against {CORPUS.cases.length} frozen test cases, and the attack lab.</>} />
      <CorpusStats />
      <CorpusGroups />
      <section className="card" aria-labelledby="lab-h">
        <CardHead marker="Lab" title="Attack lab scorecard" id="lab-h" meta={<Link href="/scorecard">Public page</Link>} />
        <div className="card-body"><ScorecardEmpty /></div>
      </section>
    </main>
  );
}
