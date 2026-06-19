// Sprint S1 (2026-06-19) — Industry presets for the OnboardingWizard Step 0.
//
// Each preset maps a vertical to a turn-key configuration:
//   - policy_packs:        IDs from services/policy/packs.py (SOC2/PCI/HIPAA/FINANCE/DEVOPS)
//   - default_capabilities: pre-checked on Step 2 of the existing wizard
//   - default_provider:     pre-selected on Step 1
//   - dashboard_preset:     stored in tenant.system_values; consumed by Dashboard.jsx (Sprint S8)
//   - blurb:                one line the founder reads while picking
//
// The "configure vs select governance" CTO complaint closes here. The user
// picks an industry once on signup and never has to think about which packs
// to enable, which approvers to wire, or which dashboard layout to pick.

import { Banknote, HeartPulse, Cpu, Sparkles, Settings2 } from 'lucide-react'

export const INDUSTRY_PRESETS = [
  {
    id: 'fintech',
    label: 'Fintech / Banking',
    icon: Banknote,
    blurb: 'Wire-transfer policies, CFO approvals, audit retention, PCI on by default.',
    policy_packs: ['SOC2', 'FINANCE', 'PCI'],
    default_capabilities: ['database', 'external_apis', 'payments'],
    default_provider: 'anthropic',
    dashboard_preset: 'finance',
    default_budget_caps: { daily_usd: 100, monthly_usd: 2500 },
  },
  {
    id: 'healthcare',
    label: 'Healthcare / HealthTech',
    icon: HeartPulse,
    blurb: 'HIPAA + SOC2 packs, patient-record approvals routed to compliance officer.',
    policy_packs: ['HIPAA', 'SOC2'],
    default_capabilities: ['database', 'internal_apis'],
    default_provider: 'anthropic',
    dashboard_preset: 'healthcare',
    default_budget_caps: { daily_usd: 50, monthly_usd: 1500 },
  },
  {
    id: 'devops',
    label: 'DevOps / Infrastructure',
    icon: Settings2,
    blurb: 'kubectl + terraform escalations, SRE LEAD approvals, runaway-loop quarantine.',
    policy_packs: ['DEVOPS', 'SOC2'],
    default_capabilities: ['infrastructure', 'filesystem', 'internal_apis'],
    default_provider: 'anthropic',
    dashboard_preset: 'devops',
    default_budget_caps: { daily_usd: 100, monthly_usd: 3000 },
  },
  {
    id: 'ai_startup',
    label: 'AI Startup / General',
    icon: Sparkles,
    blurb: 'Generic prompt-injection + budget caps + path-traversal denies. Safe defaults.',
    policy_packs: ['SOC2'],
    default_capabilities: ['database'],
    default_provider: 'anthropic',
    dashboard_preset: 'ai_startup',
    default_budget_caps: { daily_usd: 20, monthly_usd: 500 },
  },
  {
    id: 'custom',
    label: 'Custom / I\'ll configure later',
    icon: Cpu,
    blurb: 'Skip the preset. You can enable packs manually from Workspace → Settings → Policy Packs.',
    policy_packs: [],
    default_capabilities: ['database'],
    default_provider: 'anthropic',
    dashboard_preset: null,
    default_budget_caps: null,
  },
]

export function findPreset(id) {
  return INDUSTRY_PRESETS.find((p) => p.id === id) || null
}
