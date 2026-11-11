import type { Metadata } from "next";
import { Callout, PageHead } from "@/components/page";
import { PublicHeader } from "@/components/public-header";
import { CorpusGroups, CorpusStats } from "@/components/corpus";
import { CORPUS } from "@/lib/corpus";

export const metadata: Metadata = { title: "Dialback: the number-picking rule against planted attacks" };

// The corpus page (spec 0038). Static: every verdict here was written by scripts/corpus/results.py
// running the real rule; the page only lays it out.

export default function CorpusPage() {
  return (
    <div className="public">
      <PublicHeader links={[{ href: "/", label: "Front door" }, { href: "/scorecard", label: "Scorecard" }]} />
      <main>
        <PageHead titleId="corpus-h" title="Which number would Dialback call?"
          lead={`The number-picking rule against ${CORPUS.cases.length} frozen test cases`} />
        <p className="page-intro muted">Before any call, the Trust Ledger picks a number it can prove is the vendor&apos;s (R2). These are the frozen test cases
            for that one step: a fraudster plants a number in an email thread, a lookalike domain, hidden PDF text, a fake listing,
            and the rule must not pick it. Each case&apos;s expected result is frozen and its hash pinned in the repo, so it can&apos;t drift to match the code without a reviewed change.</p>
        <Callout>
          <p>
            This is the selection rule only, run by <span className="mono">{CORPUS.generated_by}</span> with{" "}
            <span className="mono">{CORPUS.rule_version}</span>. It is not the attack lab, which replays whole scams
            through calls and decisions and has its own scorecard page.
          </p>
        </Callout>

        <CorpusStats />

        <CorpusGroups />
      </main>
    </div>
  );
}
