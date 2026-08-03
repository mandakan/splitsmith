/** Human-readable byte size. Moved out of AddFootageModal so the upload
 *  queue summary and the per-file rows agree on units. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * Render a projected duration, or null when there is nothing honest to
 * show (#556).
 *
 * Guards the non-finite cases deliberately: a throughput window whose
 * rate underflows yields `Infinity`, and putting "~Infinity min" in the
 * upload dock is worse than showing no estimate at all. Sub-second
 * projections round up rather than down, because "~0 sec" reads as
 * finished while bytes are still moving.
 */
export function formatEta(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `~${Math.max(1, Math.round(seconds))} sec`;
  if (seconds < 3600) return `~${Math.round(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds - hours * 3600) / 60);
  return `~${hours} h ${minutes} min`;
}
