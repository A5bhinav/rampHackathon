// Control's Settings pages (spec 0063): what each can show from today's API. Fixed product copy
// (the nine guarantees and where they're enforced) is allowed; everything else comes from the core,
// and what the API doesn't report yet says so rather than showing a guess.
import type { CallScript, Me, Selection } from "./api-types";
import { roleLabels } from "./people.ts";

export const NOT_REPORTED = "not reported yet";

export const GUARANTEES: { id: string; rule: string; where: string }[] = [
  { id: "R1", rule: "No payment goes to changed bank details while a case is open", where: "The core holds the vendor in Ramp and reads the hold back" },
  { id: "R2", rule: "Never call a number first seen in the change request or its thread", where: "The Trust Ledger's selection rule (packages/ledger)" },
  { id: "R3", rule: "The AI collects answers; a deterministic function decides", where: "decide() and its rule table (packages/decision)" },
  { id: "R4", rule: "The voice agent never says the new bank details or what it is checking", where: "The call policy's speech guard (packages/policy)" },
  { id: "R5", rule: "The releaser is a person, and not the person who entered the change", where: "app.release_case() in Postgres" },
  { id: "R6", rule: "Every state change is written to an append-only, hash-chained log", where: "The audit schema's chain, checked by Postgres and a scheduled job" },
  { id: "R7", rule: "Errors, timeouts and unclear answers fail closed: the hold stays and a person reviews", where: "The case state machine (app.apply_case_event())" },
  { id: "R8", rule: "Every call discloses AI and recording in its first sentence", where: "The call script's fixed disclosure (packages/policy)" },
  { id: "R9", rule: "Text from documents, emails and calls is data, never instructions", where: "The ledger's reader and the call policy's tools" },
];

type Tone = "ok" | "warn" | "bad" | "muted";
export type IntegrationRow = { name: string; value: string; tone: Tone };

export function integrationRows({ kind, chain, lastCheck, alerts }: {
  kind: string;
  chain: { ok: boolean; checked: number; first_bad_seq?: number | null };
  lastCheck: { finished_at: string | null; ok: boolean | null } | null;
  alerts: readonly { severity: "info" | "warning" | "critical" }[];
}): IntegrationRow[] {
  const n = (s: string) => alerts.filter((a) => a.severity === s).length;
  const parts = [["critical", n("critical")], ["warning", n("warning")], ["info", n("info")]].filter(([, c]) => c).map(([s, c]) => `${c} ${s}`);
  return [
    { name: "Ramp", value: `${kind} dataset; mock or sandbox ${NOT_REPORTED}`, tone: "muted" },
    { name: "Evidence chain", value: chain.ok ? `intact, ${chain.checked} rows` : `broken at row #${chain.first_bad_seq ?? "?"}`, tone: chain.ok ? "ok" : "bad" },
    { name: "Scheduled chain check", value: lastCheck?.finished_at ? (lastCheck.ok ? "intact" : lastCheck.ok === false ? "found a break" : "unknown") : "not run yet",
      tone: lastCheck?.ok === false ? "bad" : lastCheck?.ok ? "ok" : "muted" },
    { name: "Open alerts", value: parts.length ? parts.join(", ") : "none", tone: n("critical") ? "bad" : n("warning") ? "warn" : "ok" },
    { name: "Webhooks", value: NOT_REPORTED, tone: "muted" },
    { name: "Voice service", value: NOT_REPORTED, tone: "muted" },
  ];
}

export function peopleRows(me: Me, demo: boolean): { name: string; roles: string; note: string }[] {
  return [
    { name: me.display_name, roles: roleLabels(me.roles), note: "You" },
    ...(demo ? [
      { name: "Demo clerk", roles: roleLabels(["clerk"]), note: "Shared demo account behind \"Viewing as\"" },
      { name: "Demo approver", roles: roleLabels(["approver"]), note: "Shared demo account behind \"Viewing as\"" },
    ] : []),
  ];
}

export function policyFrom(script: CallScript | null | undefined, selection: Pick<Selection, "rule_version" | "rule_settings"> | null | undefined) {
  return {
    disclosure: script?.disclosure ?? null,
    questions: script?.questions ?? [],
    note: script?.note ?? null,
    rule: selection ? { version: selection.rule_version, settings: selection.rule_settings } : null,
  };
}
