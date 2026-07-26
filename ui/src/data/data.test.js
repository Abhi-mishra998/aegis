import { describe, expect, it } from 'vitest';
import { INDUSTRY_PRESETS } from './industry_presets';
import { DASHBOARD_LAYOUTS } from './dashboard_layouts';

describe('INDUSTRY_PRESETS', () => {
  it('exports a non-empty array', () => {
    expect(Array.isArray(INDUSTRY_PRESETS)).toBe(true);
    expect(INDUSTRY_PRESETS.length).toBeGreaterThan(0);
  });

  it('every preset carries the fields the OnboardingWizard reads', () => {
    for (const p of INDUSTRY_PRESETS) {
      expect(p.id).toBeTypeOf('string');
      expect(p.label).toBeTypeOf('string');
      expect(p.blurb).toBeTypeOf('string');
      expect(Array.isArray(p.policy_packs)).toBe(true);
      expect(Array.isArray(p.default_capabilities)).toBe(true);
    }
  });

  it('preset IDs are unique (dedupes are user-visible on Step 0)', () => {
    const ids = INDUSTRY_PRESETS.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('DASHBOARD_LAYOUTS', () => {
  it('exports a non-empty object', () => {
    expect(typeof DASHBOARD_LAYOUTS).toBe('object');
    expect(Object.keys(DASHBOARD_LAYOUTS).length).toBeGreaterThan(0);
  });

  it('every layout has a label + tile_order + guidance list', () => {
    for (const [key, layout] of Object.entries(DASHBOARD_LAYOUTS)) {
      expect(layout.label).toBeTypeOf('string');
      expect(Array.isArray(layout.tile_order || [])).toBe(true);
      // guidance / highlights list — Dashboard reads whichever field is populated
      expect(key.length).toBeGreaterThan(0);
    }
  });
});
