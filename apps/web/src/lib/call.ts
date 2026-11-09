// The case page's call view (spec 0036). Display only: every status here is one the core sent,
// and nothing is compared or decided. The last four digits are never on the wire, so the page
// can only say how many were given (ADR-8).
import type { CallAnswerView, CallAttempt, CallScript, DecisionInfo } from "./api-types";

const WORDS: Record<string, Record<string, string>> = {
  recording_consent: { yes: "Yes", no: "No" },
  change_requested: { yes: "Yes", no: "No", unsure: "Not sure" },
  flag: {
    pressure_to_approve: "Pressed for approval", opt_out: "Asked not to be called again",
    asked_what_is_checked: "Asked what is being checked", asked_to_call_another_number: "Asked us to call another number",
    speaker_changed: "A different person took over", other: "Flagged for a person to look at",
  },
};

/** What the callee said, in words. For the last four: a count, a refusal, or "didn't know". */
export function answerText(answer: CallAnswerView): string {
  if (answer.slot === "account_last4") {
    if (answer.value === "refused") return "Refused to say";
    if (answer.value === "dont_know") return "Didn't know";
    const n = answer.digit_count;
    return typeof n === "number" && n >= 1 && n <= 4 ? `${n} digit${n === 1 ? "" : "s"} given` : "No digits recorded";
  }
  const v = answer.value ?? "";
  return WORDS[answer.slot]?.[v] ?? v.replaceAll("_", " ");
}

export type Chip = { id: string; label: string; status: string; tone: "ok" | "warn" | "bad" | "muted" };
const CHECK_TONE = { passed: "ok", not_met: "warn", not_checked: "muted" } as const;
const BLOCK_TONE: Record<string, Chip["tone"]> = { not_verified: "bad", verified: "ok" };

/** A chip per rule the answer fed that this decision reports on: its row in the checklist, or a
 *  rule that fired. Rules the decision says nothing about get no chip. */
export function ruleChips(feeds: readonly string[], decision: Pick<DecisionInfo, "checklist" | "fired"> | null): Chip[] {
  if (!decision) return [];
  const chips: Chip[] = [];
  for (const id of feeds) {
    const label = id.split("_")[0];
    const item = decision.checklist?.find((c) => c.id === id);
    const hit = decision.fired?.find((f) => f.id === id);
    if (hit && hit.block !== "verified") chips.push({ id, label, status: "fired", tone: BLOCK_TONE[hit.block] ?? "warn" });
    else if (item && item.status !== "not_checked") {  // a condition the engine never ran says nothing about this answer
      chips.push({ id, label, status: item.status.replace("_", " "), tone: CHECK_TONE[item.status] ?? "muted" });
    }
  }
  return chips;
}

export type Asked = { slot: string; wording: string; answers: CallAnswerView[] };

/** The script's questions in order, each with the answers recorded for its slot (by turn), and
 *  any answer outside the script's questions (a flag, an offered number) after them. */
// One question asks for both the name and the role; the name completes it (dialback_policy.script).
const ALSO: Record<string, string[]> = { speaker_name: ["speaker_role"] };

export function conversation(script: CallScript, answers: readonly CallAnswerView[]): { asked: Asked[]; extra: CallAnswerView[] } {
  const byTurn = [...answers].sort((x, y) => x.turn_id - y.turn_id);
  const of = (slot: string) => [slot, ...(ALSO[slot] ?? [])];
  const asked = script.questions.map((q) => ({ ...q, answers: byTurn.filter((x) => of(q.slot).includes(x.slot)) }));
  const slots = new Set(script.questions.flatMap((q) => of(q.slot)));
  return { asked, extra: byTurn.filter((x) => !slots.has(x.slot)) };
}

export type CheckRow = { label: string; word: string; ok: boolean };

/** The script checks the voice service computed, each as a word. */
export function checkRows(checks: CallAttempt["checks"]): CheckRow[] {
  if (!checks) return [];
  const count = (n: number) => (n === 0 ? "none" : String(n));
  return [
    { label: "AI and recording disclosed first", word: checks.disclosure_first ? "yes" : "no", ok: checks.disclosure_first },
    { label: "Every answer is in the turn it cites", word: checks.answers_grounded ? "yes" : "no", ok: checks.answers_grounded },
    { label: "The agent never said account digits", word: checks.agent_spoke_account_digits ? "the agent said digits" : "never said",
      ok: !checks.agent_spoke_account_digits },
    { label: "No new phone number accepted", word: checks.new_number_accepted ? "a new number was accepted" : "none accepted",
      ok: !checks.new_number_accepted },
    { label: "Policy violations", word: count(checks.policy_violations), ok: checks.policy_violations === 0 },
    { label: "Speech-guard trips", word: count(checks.speech_guard_trips), ok: checks.speech_guard_trips === 0 },
  ];
}

export const SLOT_LABEL: Record<string, string> = {
  recording_consent: "Recording consent", company_named: "Company", department: "Department", speaker_name: "Name",
  speaker_role: "Role", change_requested: "Asked for the change", account_last4: "Last four digits",
  offered_number: "Offered a number", flag: "Flag",
};
