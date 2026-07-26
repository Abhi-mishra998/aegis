// Shared display formatters. Add here rather than reinventing inline —
// audit found the same formatDollars / formatTs functions duplicated
// across BlastRadiusCard, SystemValuesTab, ShadowModeReview, ThreatGraph.

/**
 * "$1.2M" / "$45K" / "$923" — compact USD for KPI tiles.
 * Supports up to billions (B). Falsy → "$0".
 */
export function fmtUsdCompact(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000)     return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)         return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n}`;
}

/**
 * "2026-07-26 14:30" — compact UTC timestamp. `seconds` is the epoch
 * in SECONDS (Aegis's audit chain convention). Falsy/invalid → "never".
 */
export function fmtEpochShort(seconds) {
  if (!seconds || seconds <= 0) return 'never';
  try {
    return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
  } catch {
    return '—';
  }
}

/**
 * Same shape as fmtEpochShort but takes a Date-parseable value
 * (ISO string, ms epoch, Date instance) instead of an epoch-in-seconds.
 */
export function fmtDateTimeShort(value) {
  if (!value) return '—';
  try {
    return new Date(value).toISOString().slice(0, 16).replace('T', ' ');
  } catch {
    return '—';
  }
}
