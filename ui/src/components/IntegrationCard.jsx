// Sprint S7 (2026-06-19) — One integration tile on the Dashboard
// IntegrationsRow. Shows logo + label + connect-state pill + click-
// through to the right Settings page.

import React from 'react'
import { Link } from 'react-router-dom'
import { Check, ArrowRight } from 'lucide-react'

export function IntegrationCard({ icon: Icon, label, to, connected, accentColor }) {
  return (
    <Link
      to={to}
      className="flex items-center gap-3 px-3 py-3 rounded-lg border border-[var(--border-subtle)] bg-neutral-950 hover:bg-neutral-900 hover:border-white/[0.12] transition-colors group"
    >
      {Icon ? (
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
          style={{ backgroundColor: accentColor + '22', color: accentColor }}
        >
          <Icon size={18} />
        </div>
      ) : null}
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-white truncate">{label}</div>
        <div className="text-[11px] flex items-center gap-1">
          {connected ? (
            <span className="flex items-center gap-1 text-emerald-400">
              <Check size={10} className="bg-emerald-500/30 rounded-full" /> Connected
            </span>
          ) : (
            <span className="text-neutral-500">Not connected — Connect</span>
          )}
        </div>
      </div>
      <ArrowRight size={14} className="text-neutral-600 group-hover:text-white group-hover:translate-x-0.5 transition-all" />
    </Link>
  )
}
