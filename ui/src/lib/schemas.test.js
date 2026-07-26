import { describe, expect, it, vi } from 'vitest';
import { parseRule, parseRuleList } from './schemas';

describe('parseRule', () => {
  it('accepts a fully populated rule unchanged', () => {
    const raw = {
      id: 'rule-1',
      name: 'block wire >$100k',
      is_active: true,
      priority: 500,
      conditions: { window: '5m', min_violations: 3, severity_in: ['HIGH', 'CRITICAL'] },
      actions: [{ type: 'BLOCK_TOOL', tool: 'payments.wire' }],
      mode: 'auto',
    };
    const parsed = parseRule(raw);
    expect(parsed.id).toBe('rule-1');
    expect(parsed.priority).toBe(500);
    expect(parsed.mode).toBe('auto');
    expect(parsed.conditions.severity_in).toEqual(['HIGH', 'CRITICAL']);
  });

  it('drops non-string entries from severity_in', () => {
    const parsed = parseRule({
      id: 'r', name: 'n', is_active: true, priority: 0,
      conditions: { severity_in: ['HIGH', null, 42, undefined, 'LOW'] },
      actions: [],
      mode: 'auto',
    });
    expect(parsed.conditions.severity_in).toEqual(['HIGH', 'LOW']);
  });

  it('returns a safe blank rule on schema violation (missing id)', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const parsed = parseRule({ /* no id */ name: 'x' });
    expect(parsed.is_active).toBe(false);
    expect(parsed.name).toContain('contract error');
    errSpy.mockRestore();
  });
});

describe('parseRuleList', () => {
  it('returns [] for non-array input', () => {
    expect(parseRuleList(null)).toEqual([]);
    expect(parseRuleList(undefined)).toEqual([]);
    expect(parseRuleList('nope')).toEqual([]);
    expect(parseRuleList({})).toEqual([]);
  });

  it('maps each entry through parseRule (safe blank on broken items)', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const out = parseRuleList([
      { id: 'good', name: 'ok', is_active: true, priority: 0, conditions: {}, actions: [], mode: 'auto' },
      { broken: true },
    ]);
    expect(out).toHaveLength(2);
    expect(out[0].id).toBe('good');
    expect(out[1].is_active).toBe(false);
    errSpy.mockRestore();
  });
});
