import React from 'react'
import { Link } from 'react-router-dom'
import {
  Lock, Code2, HeartPulse, Radio, Zap, CreditCard, Shield, ChevronRight,
  Webhook, Users, Database, BarChart2, Calendar, Crosshair, Gauge, Key,
  FlaskConical, BookOpen,
} from 'lucide-react'

const sections = [
  {
    title: 'Access control',
    items: [
      { to: '/rbac',          label: 'RBAC Manager',    desc: 'Roles, permissions, tenant scopes',  icon: Lock },
      { to: '/users',         label: 'User Management', desc: 'Invite, manage roles, deactivate',   icon: Users },
      { to: '/security',      label: 'Security Ops',    desc: 'Authentication + secrets posture',   icon: Shield },
      { to: '/sso',           label: 'SSO Configuration', desc: 'SAML 2.0 / OIDC single sign-on',  icon: Key },
    ],
  },
  {
    title: 'Operations',
    items: [
      { to: '/system-health',    label: 'System Health',      desc: 'Service status + queue depth',       icon: HeartPulse },
      { to: '/observability',    label: 'Observability',      desc: 'Metrics, traces, SLO dashboards',    icon: Radio },
      { to: '/admin',            label: 'Admin Console',      desc: 'Platform health + tenant activity',  icon: Users },
      { to: '/policy-analytics', label: 'Policy Analytics',   desc: 'Hit rates, FP rates, coverage gaps', icon: BarChart2 },
      { to: '/quota',            label: 'Quota Management',   desc: 'Request limits + inference cost caps', icon: Gauge },
    ],
  },
  {
    title: 'Developer',
    items: [
      { to: '/developer',        label: 'Developer Panel',    desc: 'API keys, SDK examples',          icon: Code2 },
      { to: '/policy-sim',       label: 'Policy Simulation',  desc: 'Dry-run policies on historical events', icon: FlaskConical },
      { to: '/playbooks',        label: 'Playbooks',          desc: 'Automated incident response sequences',  icon: BookOpen },
      { to: '/webhook-settings', label: 'Webhook Settings',   desc: 'Slack, PagerDuty, generic hooks', icon: Webhook },
      { to: '/siem',             label: 'SIEM Integration',   desc: 'Splunk HEC + Datadog Logs push',  icon: Database },
      { to: '/threat-intel',     label: 'Threat Intelligence', desc: 'IP + domain enrichment feeds',   icon: Crosshair },
      { to: '/scheduled-reports', label: 'Scheduled Reports', desc: 'Automated PDF delivery to stakeholders', icon: Calendar },
    ],
  },
  {
    title: 'Account',
    items: [
      { to: '/billing',       label: 'Usage & Billing', desc: 'Consumption, invoices, plan',        icon: CreditCard },
      { to: '/risk',          label: 'Risk Engine (preview)', desc: 'Behavioral scoring — experimental', icon: Zap },
    ],
  },
]

export default function Settings() {
  return (
    <div className="max-w-5xl mx-auto">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold text-white mb-1">Settings</h1>
        <p className="text-sm text-neutral-400">
          Administrative surfaces and developer tooling. Daily security workflows live in
          Flight Recorder, Policies, Audit Trail, and Incidents.
        </p>
      </header>

      <div className="space-y-8">
        {sections.map((section) => (
          <section key={section.title}>
            <h2 className="text-[11px] uppercase tracking-wider text-neutral-500 mb-3">
              {section.title}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {section.items.map((item) => (
                <Link
                  key={item.to}
                  to={item.to}
                  className="
                    group flex items-start gap-3 p-4 rounded-lg
                    bg-[var(--bg-surface)] border border-[var(--border-subtle)]
                    hover:border-white/20 hover:bg-white/[0.03]
                    transition-all
                  "
                >
                  <div className="w-9 h-9 rounded-md bg-white/[0.04] flex items-center justify-center shrink-0">
                    <item.icon size={16} className="text-neutral-400 group-hover:text-white" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-white">{item.label}</span>
                      <ChevronRight size={14} className="text-neutral-600 group-hover:text-white shrink-0" />
                    </div>
                    <p className="text-xs text-neutral-500 mt-0.5">{item.desc}</p>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
