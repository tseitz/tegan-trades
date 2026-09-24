import { useEffect } from "react";

export function capitalize(s: string): string {
  return s.length === 0 ? s : s[0].toUpperCase() + s.slice(1);
}

export function useDocumentTitle(screen: string): void {
  useEffect(() => {
    document.title = `Tegan Trades: ${screen}`;
  }, [screen]);
}
