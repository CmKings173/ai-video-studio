/**
 * Computes progress percentage (0 - 100) safely from live SSE or counters.
 *
 * Rules:
 * - If live SSE percentage exists (number >= 0), use it.
 * - Else if total > 0, compute (current / total) * 100.
 * - Else return 0.
 */
export function getProgressPercent(
  livePercent?: number | null,
  current?: number | null,
  total?: number | null
): number {
  if (typeof livePercent === "number" && !isNaN(livePercent) && livePercent >= 0) {
    return Math.min(100, Math.max(0, Math.round(livePercent)));
  }

  if (typeof total === "number" && total > 0) {
    const cur = typeof current === "number" && !isNaN(current) ? current : 0;
    return Math.min(100, Math.max(0, Math.round((cur / total) * 100)));
  }

  return 0;
}
