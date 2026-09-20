import type { components } from "./schema";

export type MandateList = components["schemas"]["MandateList"];
export type ReviewDocument = components["schemas"]["ReviewDocument"];
export type ReviewHeader = components["schemas"]["ReviewHeader"];
export type LevelsSection = components["schemas"]["LevelsSection"];

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
