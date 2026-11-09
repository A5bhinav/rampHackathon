import "server-only";
import { redirect } from "next/navigation";
import { sessionToken } from "./session";
import type {
  ActionResult, Alert, ApiError, CaseDetail, CaseSummary, ChainStatus, Dataset, Me, SelectionView, VendorRisk,
} from "./api-types";

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
  if (!response.ok) throw new CoreError(response.status, body as ApiError);
  return body as T;
}

export const core = {
  me: () => call<Me>("/api/me"),
  datasets: () => call<{ datasets: Dataset[] }>("/api/datasets").then((r) => r.datasets),
  chain: (datasetId: string) => call<ChainStatus>(`/api/datasets/${datasetId}/chain`),
  alerts: (datasetId: string) => call<{ alerts: Alert[] }>(`/api/datasets/${datasetId}/alerts`).then((r) => r.alerts),
  vendorRisk: (datasetId: string) =>
    call<{ vendors: VendorRisk[] }>(`/api/datasets/${datasetId}/vendor-risk`).then((r) => r.vendors),
  cases: (datasetId: string) =>
    call<{ cases: CaseSummary[] }>(`/api/cases?dataset_id=${encodeURIComponent(datasetId)}`).then((r) => r.cases),
  case: (caseId: string) => call<CaseDetail>(`/api/cases/${caseId}`),
  selection: (caseId: string) => call<SelectionView>(`/api/cases/${caseId}/selection`),
  release: (caseId: string, body: { expected_version: number; action: "approve" | "reject"; reason: string }) =>
    call<ActionResult>(`/api/cases/${caseId}/release`, { method: "POST", body: JSON.stringify(body) }),
  review: (caseId: string, body: { expected_version: number; action: "clear" | "block"; reason: string }) =>
    call<ActionResult>(`/api/cases/${caseId}/review`, { method: "POST", body: JSON.stringify(body) }),
};
