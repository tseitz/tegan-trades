import { useDocumentTitle } from "./documentTitle";

export function App() {
  useDocumentTitle("Mandates");
  return <p className="text-muted">Pick a Mandate above.</p>;
}
