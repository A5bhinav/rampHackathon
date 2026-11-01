import type { CaseState, ChainStatus } from "@/lib/api-types";
import { words } from "@/lib/format";

const STATE_TONE: Record<CaseState, string> = {
  open: "info", held: "info", calling: "info", retry_wait: "info", deciding: "info",
  awaiting_release: "ok", needs_review: "warn", blocked: "bad", releasing: "ok", released: "muted",
};

export function StateBadge({ state }: { state: CaseState }) {
  return <span className={`badge tone-${STATE_TONE[state]}`}>{words(state)}</span>;
}

const OUTCOME_TONE: Record<string, string> = { verified: "ok", not_verified: "bad", needs_review: "warn" };

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="muted">—</span>;
  return <span className={`badge tone-${OUTCOME_TONE[outcome] ?? "muted"}`}>{words(outcome)}</span>;
}

export function ChainBadge({ chain }: { chain: ChainStatus }) {
  if (chain.ok) {
    return <span className="badge tone-ok" title={`${chain.checked} events verified`}>Chain intact · {chain.checked} events</span>;
  }
  return <span className="badge tone-bad" title={chain.detail}>Chain broken at event #{chain.first_bad_seq}</span>;
}
