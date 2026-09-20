import type { ReviewDocument } from "../api/client";

export type ReviewRow = ReviewDocument["grid"]["rows"][number];

export type SortDirection = "asc" | "desc";

export interface GridSort {
  column: number;
  direction: SortDirection;
}

export function isNumericColumn(rows: readonly ReviewRow[], column: number): boolean {
  return rows.some((row) => typeof row.cells[column]?.value === "number");
}

export function nextSort(
  rows: readonly ReviewRow[],
  current: GridSort | null,
  column: number,
): GridSort | null {
  const firstDirection: SortDirection = isNumericColumn(rows, column) ? "desc" : "asc";
  if (!current || current.column !== column) {
    return { column, direction: firstDirection };
  }
  if (current.direction === firstDirection) {
    return { column, direction: firstDirection === "asc" ? "desc" : "asc" };
  }
  return null;
}

export function sortRows(rows: readonly ReviewRow[], sort: GridSort | null): readonly ReviewRow[] {
  if (!sort) return rows;
  const { column, direction } = sort;
  const numeric = isNumericColumn(rows, column);
  const sign = direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const cellA = a.cells[column];
    const cellB = b.cells[column];
    if (numeric) {
      // `value?: number | null` — `!== null` alone still leaves `undefined`, which fails
      // TS18048 on the subtraction below under `strict`.
      const valueA = typeof cellA.value === "number" ? cellA.value : null;
      const valueB = typeof cellB.value === "number" ? cellB.value : null;
      // Nulls sort last in both directions, so the sign only applies once both are numbers.
      if (valueA === null && valueB === null) return 0;
      if (valueA === null) return 1;
      if (valueB === null) return -1;
      return (valueA - valueB) * sign;
    }
    return cellA.text.localeCompare(cellB.text) * sign;
  });
}
