import type { components } from "./schema";

export type MandateList = components["schemas"]["MandateList"];

export async function fetchMandates(): Promise<MandateList> {
  const response = await fetch("/api/mandates");
  if (!response.ok) {
    throw new Error(`GET /api/mandates failed: ${response.status}`);
  }
  return response.json() as Promise<MandateList>;
}
