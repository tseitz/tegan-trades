import { useParams } from "react-router-dom";

export function MandatePage() {
  const { name } = useParams<{ name: string }>();

  return (
    <div>
      <h1>{name}</h1>
      <p>This screen is not built yet.</p>
    </div>
  );
}
