import "server-only";
import { redirect } from "next/navigation";
import { sessionToken } from "./session";
import type {
  ActionResult, AdminLog, Alert, ApiError, BankChangeResult, CaseDetail, CaseState, CaseSummary, ChainStatus, Dataset, DemoState,
  EvidenceLog, Me, PersonAdminView, Role, SelectionView, VendorAccounts, VendorRisk,
} from "./api-types";
import { ACCESS_OFF } from "./people";

// The only way the dashboard talks to the core: server-side, with the signed-in user's
// token, against packages/contracts/openapi/dialback.yaml. The browser never calls the core.

const CORE_URL = (process.env.CORE_URL ?? "http://localhost:8001").replace(/\/$/, "");

export class CoreError extends Error {
  constructor(public status: number, public body: ApiError) {
    super(body.message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await sessionToken();
  if (!token) redirect("/sign-in");
  const response = await fetch(`${CORE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...init.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (response.status === 401) redirect("/sign-in?expired=1");
  const body = await response.json();
  if (response.status === 403 && (body as ApiError).message === ACCESS_OFF) redirect("/access-off");  // spec 0095, D7
  if (!response.ok) throw new CoreError(response.status, body as ApiError);
  return body as T;
}

const ALL_STATES = (["open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked",
  "releasing", "released"] satisfies CaseState[]).map((s) => `state=${s}`).join("&");

export const core = {
  me: () => call<Me>("/api/me"),
  datasets: () => call<{ datasets: Dataset[] }>("/api/datasets").then((r) => r.datasets),
  evidence: (datasetId: string) => call<EvidenceLog>(`/api/datasets/${datasetId}/evidence`),
  chain: (datasetId: string) => call<ChainStatus>(`/api/datasets/${datasetId}/chain`),
  alerts: (datasetId: string) => call<{ alerts: Alert[] }>(`/api/datasets/${datasetId}/alerts`).then((r) => r.alerts),
  vendorRisk: (datasetId: string) =>
    call<{ vendors: VendorRisk[] }>(`/api/datasets/${datasetId}/vendor-risk`).then((r) => r.vendors),
  cases: (datasetId: string) =>
    call<{ cases: CaseSummary[] }>(`/api/cases?dataset_id=${encodeURIComponent(datasetId)}`).then((r) => r.cases),
  /** Every case on the dataset, released ones included (the demo links, spec 0033). */
  allCases: (datasetId: string) =>
    call<{ cases: CaseSummary[] }>(`/api/cases?dataset_id=${encodeURIComponent(datasetId)}&limit=200&${ALL_STATES}`).then((r) => r.cases),
  case: (caseId: string) => call<CaseDetail>(`/api/cases/${caseId}`),
  selection: (caseId: string) => call<SelectionView>(`/api/cases/${caseId}/selection`),
  release: (caseId: string, body: { expected_version: number; action: "approve" | "reject"; reason: string }) =>
    call<ActionResult>(`/api/cases/${caseId}/release`, { method: "POST", body: JSON.stringify(body) }),
  /** The demo's Ramp (mock) portal (spec 0077): each vendor's current account, and whether this person can change one. */
  vendorAccounts: (datasetId: string) => call<VendorAccounts>(`/api/datasets/${encodeURIComponent(datasetId)}/vendor-accounts`),
  /** A clerk's bank change, sent by the core to the Ramp mock as their linked Ramp user. The account number goes
   *  straight through; the answer carries only routing and last four. */
  bankChange: (datasetId: string, vendorId: string, body: { routing_number: string; account_number: string }) =>
    call<BankChangeResult>(`/api/datasets/${encodeURIComponent(datasetId)}/vendors/${encodeURIComponent(vendorId)}/bank-change`, { method: "POST", body: JSON.stringify(body) }),
  review: (caseId: string, body: { expected_version: number; action: "clear" | "block"; reason: string }) =>
    call<ActionResult>(`/api/cases/${caseId}/review`, { method: "POST", body: JSON.stringify(body) }),
  // The admin's API (spec 0095). The database checks the role on every call.
  people: () => call<{ people: PersonAdminView[] }>("/api/people").then((r) => r.people),
  changeRole: (userId: string, body: { role: Role; grant: boolean; reason: string }) =>
    call<PersonAdminView>(`/api/people/${encodeURIComponent(userId)}/roles`, { method: "POST", body: JSON.stringify(body) }),
  changeRampLink: (userId: string, body: { ramp_user_id: string | null; reason: string }) =>
    call<PersonAdminView>(`/api/people/${encodeURIComponent(userId)}/ramp-link`, { method: "POST", body: JSON.stringify(body) }),
  changeAccess: (userId: string, body: { enabled: boolean; reason: string }) =>
    call<PersonAdminView>(`/api/people/${encodeURIComponent(userId)}/access`, { method: "POST", body: JSON.stringify(body) }),
  adminLog: () => call<AdminLog>("/api/admin/log?limit=50"),
  demoState: () => call<DemoState>("/api/admin/demo"),
  demoReset: (body: { link_clerk_user_id: string | null; reason: string }) =>
    call<DemoState>("/api/admin/demo/reset", { method: "POST", body: JSON.stringify(body) }),
  demoChaos: (body: { enabled: boolean; reason: string }) =>
    call<DemoState>("/api/admin/demo/chaos", { method: "POST", body: JSON.stringify(body) }),
  demoTamper: (body: { confirm: "break the chain"; reason: string }) =>
    call<DemoState>("/api/admin/demo/tamper", { method: "POST", body: JSON.stringify(body) }),
};
