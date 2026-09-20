import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchMandates, type MandateList } from "./api/client";

export function App() {
  const [mandates, setMandates] = useState<MandateList["mandates"]>([]);

  useEffect(() => {
    fetchMandates().then((list) => setMandates(list.mandates));
  }, []);

  return (
    <div>
      <h1>Mandates</h1>
      <ul>
        {mandates.map((mandate) => (
          <li key={mandate.name}>
            <Link to={`/mandate/${mandate.name}`}>{mandate.name}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
