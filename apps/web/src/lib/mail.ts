// What the email door did with each email, in one plain sentence per value (spec 0089).
// Each map is typed over the contract's enum, so the compiler refuses a new value without a label.
import type { MailOutcome, RedFlag, ReplyStatus } from "./api-types";

const OUTCOME: Record<MailOutcome, string> = {
  pending: "Received, not yet looked at.",
  not_flagged: "Not a bank-change request, so nothing was done.",
  door_off: "The email door is off for this dataset, so nothing was done.",
  not_demo_vendor: "The sender is not one of the vendors the door acts on.",
  no_vendor: "No vendor could be matched to this email.",
  several_vendors: "More than one vendor matched, so nothing was done.",
  no_routing_number: "The email gave no routing number, so no case was opened.",
  no_account_number: "The email gave no account number, so no case was opened.",
  same_as_on_file: "The details match what is already on file, so nothing changed.",
  repeat_request: "The same request was already handled, so nothing new was done.",
  case_opened: "A case was opened for this request.",
  joined_case: "The request was added to a case that was already open.",
  ramp_case_open: "Not added: this vendor's open case has a change made in Ramp, which the call checks instead.",
  failed: "Dialback could not process this email.",
};

const REPLY: Record<ReplyStatus, string> = {
  not_flagged: "No reply, because the email was not a bank-change request.",
  sent: "A reply went to the sender.",
  not_configured: "No reply was sent, because sending mail is not set up.",
  unauthenticated: "No reply was sent, because the sender could not be verified.",
  suppressed: "No reply was sent, because the address is on the suppression list.",
  rate_limited: "No reply was sent, because the sender has had too many replies lately.",
  failed: "The reply could not be sent.",
};

const FLAG: Record<RedFlag, string> = {
  lookalike_domain: "The sender's domain looks like the vendor's, but is not the same.",
  sender_off_domain: "The sender is not on the vendor's domain.",
  reply_to_differs: "The reply-to address differs from the sender's.",
  sender_unauthenticated: "The email failed its sender checks.",
  unknown_phone: "The email gives a phone number we have no record of.",
};

export const outcomeLabel = (o: MailOutcome): string => OUTCOME[o];
export const replyLabel = (r: ReplyStatus): string => REPLY[r];
export const flagLabel = (f: RedFlag): string => FLAG[f];
