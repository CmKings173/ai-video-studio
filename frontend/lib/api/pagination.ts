import type { Page } from "./types";

/** Keep size in caller/query-key inputs; the backend wire contract uses page_size.
 * Omitting size preserves the backend default of 20. */
export function paginationParams<T extends { page?: number; size?: number }>(params?: T) {
  const { size, ...filters } = params ?? {};
  if (size !== undefined && (!Number.isInteger(size) || size < 1 || size > 100)) {
    throw new RangeError("Page size must be an integer between 1 and 100");
  }
  return { ...filters, ...(size === undefined ? {} : { page_size: size }) };
}

export function nextPageParam<T>(lastPage: Page<T>): number | undefined {
  return lastPage.page * lastPage.page_size < lastPage.total ? lastPage.page + 1 : undefined;
}

export function flattenPageItems<T extends { id: string }>(pages: readonly Page<T>[] | undefined): T[] {
  const seen = new Set<string>();
  return (pages ?? []).flatMap((page) => page.items).filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}
