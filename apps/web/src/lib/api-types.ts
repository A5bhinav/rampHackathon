// Types generated from packages/contracts/openapi/dialback.yaml (`pnpm gen:api`).
// Never edit the generated file; change the contract and regenerate.
import type { components } from "@contracts/dialback";

type S = components["schemas"];
export type Me = S["Me"];
export type Dataset = S["Dataset"];
export type ChainStatus = S["ChainStatus"];
export type Alert = S["Alert"];
export type CaseSummary = S["CaseSummary"];
export type CaseDetail = S["CaseDetail"];
export type TimelineEntry = S["TimelineEntry"];
export type ActionResult = S["ActionResult"];
export type ApiError = S["Error"];
export type CaseState = S["CaseState"];
export type DecisionInfo = S["DecisionInfo"];
export type SelectionView = S["SelectionView"];
export type Selection = S["selection.schema"];
export type Candidate = Selection["candidates"][number];
export type VendorRisk = S["VendorRisk"];
