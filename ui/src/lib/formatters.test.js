import { describe, expect, it } from 'vitest';
import { fmtUsdCompact, fmtEpochShort, fmtDateTimeShort } from './formatters';

describe('fmtUsdCompact', () => {
  it('handles null / undefined / non-numeric as $0', () => {
    expect(fmtUsdCompact(null)).toBe('$0');
    expect(fmtUsdCompact(undefined)).toBe('$0');
    expect(fmtUsdCompact('not-a-number')).toBe('$0');
  });

  it('leaves sub-thousand values with no suffix', () => {
    expect(fmtUsdCompact(0)).toBe('$0');
    expect(fmtUsdCompact(1)).toBe('$1');
    expect(fmtUsdCompact(999)).toBe('$999');
  });

  it('formats thousands with a K suffix (no decimals)', () => {
    expect(fmtUsdCompact(1_000)).toBe('$1K');
    expect(fmtUsdCompact(45_500)).toBe('$46K');
    expect(fmtUsdCompact(999_999)).toBe('$1000K');
  });

  it('formats millions with an M suffix (one decimal)', () => {
    expect(fmtUsdCompact(1_000_000)).toBe('$1.0M');
    expect(fmtUsdCompact(12_500_000)).toBe('$12.5M');
  });

  it('formats billions with a B suffix (one decimal)', () => {
    expect(fmtUsdCompact(1_000_000_000)).toBe('$1.0B');
    expect(fmtUsdCompact(2_500_000_000)).toBe('$2.5B');
  });
});

describe('fmtEpochShort', () => {
  it('returns "never" for falsy or negative epoch', () => {
    expect(fmtEpochShort(0)).toBe('never');
    expect(fmtEpochShort(null)).toBe('never');
    expect(fmtEpochShort(undefined)).toBe('never');
    expect(fmtEpochShort(-1)).toBe('never');
  });

  it('formats a valid epoch (seconds) as YYYY-MM-DD HH:MM', () => {
    // 2026-07-26T14:30:00Z
    const epoch = Date.UTC(2026, 6, 26, 14, 30, 0) / 1000;
    expect(fmtEpochShort(epoch)).toBe('2026-07-26 14:30');
  });
});

describe('fmtDateTimeShort', () => {
  it('returns em-dash for falsy input', () => {
    expect(fmtDateTimeShort(null)).toBe('—');
    expect(fmtDateTimeShort(undefined)).toBe('—');
    expect(fmtDateTimeShort('')).toBe('—');
  });

  it('formats an ISO string as YYYY-MM-DD HH:MM', () => {
    expect(fmtDateTimeShort('2026-07-26T14:30:00Z')).toBe('2026-07-26 14:30');
  });

  it('formats a Date instance', () => {
    const d = new Date(Date.UTC(2026, 0, 1, 0, 0, 0));
    expect(fmtDateTimeShort(d)).toBe('2026-01-01 00:00');
  });
});
