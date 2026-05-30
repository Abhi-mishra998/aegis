import { z } from 'zod'

// Keeps only string items — drops nulls, numbers, and any other non-string
const safeStringList = z.preprocess(
  (v) => Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [],
  z.array(z.string()),
)

// Keeps only plain objects — safe for action blobs with arbitrary shape
const safeObjectList = z.preprocess(
  (v) => Array.isArray(v) ? v.filter((x) => x !== null && typeof x === 'object' && !Array.isArray(x)) : [],
  z.array(z.record(z.string(), z.any())),
)

// ── ARE Conditions ────────────────────────────────────────────────────────────
// Schemas are intentionally not exported — they are an implementation detail
// of parseRule() / parseRuleList() and not part of the module's public API.

const AREConditionsSchema = z.object({
  window:          z.string().default('5m'),
  min_violations:  z.number().int().min(1).default(1),
  severity_in:     safeStringList,
  risk_score_gte:  z.number().min(0).max(1).default(0),
  tool_in:         safeStringList,
  agent_id:        z.string().default('*'),
  repeat_offender: z.boolean().default(false),
}).passthrough()

// ── ARE Rule ──────────────────────────────────────────────────────────────────

const AutoResponseRuleSchema = z.object({
  id:                    z.string(),
  tenant_id:             z.string().optional(),
  name:                  z.string(),
  is_active:             z.boolean().default(true),
  priority:              z.number().int().default(0),
  conditions:            z.preprocess(
    (v) => (v !== null && typeof v === 'object' && !Array.isArray(v) ? v : {}),
    AREConditionsSchema,
  ),
  actions:               safeObjectList,
  cooldown_seconds:      z.number().int().default(300),
  max_triggers_per_hour: z.number().int().default(10),
  stop_on_match:         z.boolean().default(true),
  mode:                  z.enum(['auto', 'manual', 'suggest']).default('auto'),
  version:               z.number().int().default(1),
  trigger_count:         z.number().int().default(0),
  false_positive_count:  z.number().int().default(0),
  suppressed_until:      z.string().nullable().optional(),
  last_triggered_at:     z.string().nullable().optional(),
  created_at:            z.string().optional(),
  updated_at:            z.string().optional(),
}).passthrough()

// ── Safe blank — returned when parse fails so the UI gets valid structure ─────

const BLANK_CONDITIONS = {
  window: '5m', min_violations: 1, severity_in: [], risk_score_gte: 0,
  tool_in: [], agent_id: '*', repeat_offender: false,
}

function blankRule(id) {
  return {
    id: id || 'unknown', tenant_id: '', name: '⚠ contract error', is_active: false,
    priority: 0, conditions: BLANK_CONDITIONS, actions: [],
    cooldown_seconds: 300, max_triggers_per_hour: 10, stop_on_match: true,
    mode: 'auto', version: 0, trigger_count: 0, false_positive_count: 0,
    suppressed_until: null, last_triggered_at: null,
  }
}

// ── Parse helpers ─────────────────────────────────────────────────────────────
// On parse failure: log the contract violation, return a structurally safe blank.
// The UI will render it as inactive (is_active: false) rather than crash or show
// corrupt data.

export function parseRule(raw) {
  const result = AutoResponseRuleSchema.safeParse(raw)
  if (!result.success) {
    console.error('[ACP contract violation] AutoResponseRule', result.error.issues, raw)
    return blankRule(raw?.id)
  }
  return result.data
}

export function parseRuleList(rawList) {
  if (!Array.isArray(rawList)) return []
  return rawList.map(parseRule)
}
