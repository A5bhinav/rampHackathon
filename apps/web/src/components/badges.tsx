import type { CaseState, ChainStatus } from "@/lib/api-types";
import { stateLabel, stateTone } from "@/lib/states";
import { words } from "@/lib/format";

export function StateBadge({ state }: { state: CaseState }) {
  return <span className={`badge tone-${stateTone(state)}`}>{stateLabel(state)}</span>;
}

const OUTCOME: Record<string, { tone: string; label: string }> = {
  verified: { tone: "ok", label: "Verified" },
  not_verified: { tone: "bad", label: "Not verified" },
  needs_review: { tone: "warn", label: "Needs review" },
};

export function outcomeOf(outcome: string): { tone: string; label: string } {
  return Object.hasOwn(OUTCOME, outcome) ? OUTCOME[outcome] : { tone: "muted", label: words(outcome) };
}

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="muted">—</span>;
  const o = outcomeOf(outcome);
  return <span className={`badge tone-${o.tone}`}>{o.label}</span>;
}

export function ChainBadge({ chain }: { chain: ChainStatus }) {
  if (chain.ok) {
    return <span className="badge tone-ok" title={`${chain.checked} events verified`}>Chain intact</span>;
  }
  return <span className="badge tone-bad" title={chain.detail}>Chain broken at #{chain.first_bad_seq}</span>;
}
