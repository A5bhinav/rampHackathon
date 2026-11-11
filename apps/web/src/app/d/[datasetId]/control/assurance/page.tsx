import Link from "next/link";
import { CorpusGroups, CorpusStats } from "@/components/corpus";
import { PageHead } from "@/components/page";
import { LabResults } from "@/components/scorecard";
import { CORPUS } from "@/lib/corpus";

// Assurance (specs 0063, 0101): what's been tested, inside Control. The number-picking rule against
// the frozen corpus, and one real run of the attack lab.
export default function AssurancePage() {
  return (
    <main>
      <PageHead eyebrow="Control · testing" title="Assurance"
        lead={<>The selection rule, <span className="mono">{CORPUS.rule_version}</span>, against {CORPUS.cases.length} frozen test cases, and the attack lab.</>} />
      <CorpusStats />
      <CorpusGroups />
      <div className="group-head">
        <h2 id="lab-h">The attack lab</h2>
        <p className="muted">One real run, failures included. <Link href="/scorecard">Public page</Link></p>
      </div>
      <LabResults />
    </main>
  );
}
