import type { components } from "./schema";

export type MandateList = components["schemas"]["MandateList"];
export type ReviewDocument = components["schemas"]["ReviewDocument"];
export type ReviewHeader = components["schemas"]["ReviewHeader"];
export type LevelsSection = components["schemas"]["LevelsSection"];
export type AltSignalSection = components["schemas"]["AltSignalSection"];
export type RefreshJobStatus = components["schemas"]["RefreshJobStatus"];
export type TreasuryResponse = components["schemas"]["TreasuryResponse"];

export async function fetchMandates(): Promise<MandateList> {
  const response = await fetch("/api/mandates");
  if (!response.ok) {
    throw new Error(`GET /api/mandates failed: ${response.status}`);
  }
  return response.json() as Promise<MandateList>;
}

export async function fetchReview(name: string): Promise<ReviewDocument> {
  const response = await fetch(`/api/mandates/${name}/review`);
  if (!response.ok) {
    throw new Error(`GET /api/mandates/${name}/review failed: ${response.status}`);
  }
  return response.json() as Promise<ReviewDocument>;
}

export async function fetchTreasury(): Promise<TreasuryResponse> {
  const response = await fetch("/api/treasury");
  if (!response.ok) {
    throw new Error(`GET /api/treasury failed: ${response.status}`);
  }
  return response.json() as Promise<TreasuryResponse>;
}

export async function startRefresh(): Promise<RefreshJobStatus> {
  const response = await fetch("/api/refresh", { method: "POST" });
  if (!response.ok) {
    throw new Error(`POST /api/refresh failed: ${response.status}`);
  }
  return response.json() as Promise<RefreshJobStatus>;
}

export async function fetchRefreshStatus(id: string): Promise<RefreshJobStatus> {
  const response = await fetch(`/api/refresh/${id}`);
  if (!response.ok) {
    throw new Error(`GET /api/refresh/${id} failed: ${response.status}`);
  }
  return response.json() as Promise<RefreshJobStatus>;
}
