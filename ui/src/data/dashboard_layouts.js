// Sprint S8 (2026-06-19) — Per-industry Dashboard layouts.
//
// A founder picks an industry in the OnboardingWizard Step 0 (Sprint S1)
// and the chosen preset stores `dashboard_preset` in tenant.system_values.
// Dashboard.jsx reads that value here and renders an industry-flavored
// guidance row + (future) re-orders the KPI tiles so the metrics that
// matter most to that industry surface first.
//
// Today's MVP is the guidance row — a "What to watch in <industry>"
// pill that pre-selects 3-4 KPIs as the highlight set. Re-ordering
// the existing MetricTile grid based on `tile_order` is a follow-up
// once we get a real Fintech / DevOps / Healthcare design partner
// to dictate the exact ordering. Shipping the data file + render hook
// today means S8 is a pure UI swap then.

import { Banknote, HeartPulse, Settings2, Sparkles } from 'lucide-react'

export const DASHBOARD_LAYOUTS = {
  finance: {
    label: 'Finance',
    icon: Banknote,
    accent_color: '#0d9488',
    headline: 'Wire-transfer escalations, CFO approval queue, monthly spend.',
    watch_tiles: ['escalations_pending', 'monthly_ai_spend', 'wire_transfers_escalated', 'pii_lookups'],
    tile_order: ['escalations_pending', 'monthly_ai_spend', 'wire_transfers_escalated', 'allowed_30d', 'denied_30d', 'active_findings'],
  },
  healthcare: {
    label: 'Healthcare',
    icon: HeartPulse,
    accent_color: '#dc2626',
    headline: 'PHI access denials, patient-record approvals, HIPAA evidence count.',
    watch_tiles: ['phi_denials', 'patient_record_escalations', 'soc2_evidence_rows', 'denied_30d'],
    tile_order: ['phi_denials', 'denied_30d', 'escalations_pending', 'protected_agents', 'allowed_30d', 'active_findings'],
  },
  devops: {
    label: 'DevOps',
    icon: Settings2,
    accent_color: '#f97316',
    headline: 'kubectl + terraform escalations, prod-namespace blocks, runaway-loop quarantines.',
    watch_tiles: ['kubectl_escalations', 'terraform_blocks', 'runaway_loops', 'active_findings'],
    tile_order: ['active_findings', 'denied_30d', 'escalations_pending', 'protected_agents', 'allowed_30d', 'monthly_ai_spend'],
  },
  ai_startup: {
    label: 'AI Startup',
    icon: Sparkles,
    accent_color: '#8b5cf6',
    headline: 'Path-traversal denies, prompt-injection blocks, budget burn.',
    watch_tiles: ['path_traversal_denials', 'prompt_injection_blocks', 'monthly_ai_spend', 'active_findings'],
    tile_order: ['monthly_ai_spend', 'protected_agents', 'allowed_30d', 'denied_30d', 'escalations_pending', 'active_findings'],
  },
}

export function getLayout(preset_id) {
  return DASHBOARD_LAYOUTS[preset_id] || null
}
