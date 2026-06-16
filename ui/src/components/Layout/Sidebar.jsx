import React, { useEffect, useRef, useState, useCallback } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  Users, Shield, FileText, X, Power, Zap,
  LogOut, Terminal, BarChart2,
  GitMerge, AlertTriangle, Crosshair, Bot,
  Network, Film, ShieldCheck, ChevronDown, ChevronRight, Settings as SettingsIcon,
  CreditCard, Radio, Bell, BookOpen,
  Workflow, MessagesSquare, Gauge, HeartPulse, DollarSign, Share2,
  Beaker, EyeOff, Inbox,
} from 'lucide-react'
import { authService, notificationService } from '../../services/api'
import { useAuth } from '../../hooks/useAuth'
import { useRole } from '../../hooks/useRole'
import AgentScopePicker from './AgentScopePicker'

// Sprint 6 — 3-tier nav per PRODUCT_PLAN §12.8.
//
//   Primary (6 items, always visible)
//   Advanced (10 items, collapsed by default — analyst tools)
//   Admin (4 items, OWNER/ADMIN only)
//
// Hotkeys mirror App.jsx's <GlobalShortcuts>. Items deleted in Sprint 6
// (LiveDemo, Pricing, ExecutiveDashboard) are gone from every tier.

const primaryNav = [
  { path: '/dashboard',       label: 'Dashboard',  icon: Gauge,         hint: 'G D' },
  { path: '/agents',          label: 'Agents',     icon: Users,         hint: 'G A' },
  // Sprint 17 — Aegis for Teams. Sits between Agents (production AI
  // agents, SDK-on-endpoint) and Incidents so the operator can swing
  // between "who's writing my agents" and "who's using my employees'
  // Claude keys" without leaving the primary nav.
  { path: '/team',            label: 'Team',       icon: Users,         hint: 'G M' },
  { path: '/incidents',       label: 'Incidents',  icon: AlertTriangle, hint: 'G I' },
  { path: '/live-feed',       label: 'Live Feed',  icon: Radio,         hint: 'G L' },
  { path: '/policies',        label: 'Policies',   icon: GitMerge,      hint: 'G P' },
  { path: '/settings',        label: 'Settings',   icon: SettingsIcon,  hint: 'G S' },
]

const advancedNav = [
  { path: '/audit-logs',        label: 'Audit Logs',       icon: BarChart2 },
  { path: '/forensics',         label: 'Forensics',        icon: FileText  },
  { path: '/observability',     label: 'Observability',    icon: Radio     },
  { path: '/playground',        label: 'Agent Playground', icon: Terminal  },
  { path: '/threat-intel',      label: 'Threat Intel',     icon: Crosshair },
  { path: '/evaluation',        label: 'Evaluation',       icon: Beaker    },
  { path: '/playbooks',         label: 'Playbooks',        icon: BookOpen  },
  { path: '/auto-response',     label: 'Auto-Response',    icon: Bot       },
  { path: '/identity-graph',    label: 'Identity Graph',   icon: Network,  hint: 'G G' },
  { path: '/threat-graph',      label: 'Threat Graph',     icon: Crosshair, hint: 'G T' },
  { path: '/shadow-mode',       label: 'Shadow Mode',      icon: EyeOff    },
  { path: '/shadow-review',     label: 'Shadow Review',    icon: ShieldCheck },
  { path: '/flight-recorder',   label: 'Flight Recorder',  icon: Film,     hint: 'G F' },
  { path: '/decision-explorer', label: 'Decision Explorer', icon: Workflow, hint: 'G E' },
  { path: '/session-explorer',  label: 'Session Explorer', icon: MessagesSquare },
  { path: '/approval-inbox',    label: 'Approval Inbox',   icon: Inbox     },
  { path: '/fleet',             label: 'Fleet',            icon: HeartPulse },
]

const adminNav = [
  { path: '/system-health', label: 'System Health', icon: HeartPulse },
  { path: '/billing',       label: 'Billing',       icon: CreditCard },
  { path: '/compliance',    label: 'Compliance',    icon: Shield     },
]

const killSwitchItem = { path: '/kill-switch', label: 'Kill Switch', icon: Power, danger: true }

export default function Sidebar({ isOpen, onClose }) {
  const location  = useLocation()
  const navigate  = useNavigate()
  const { updateAuth, isAuthenticated } = useAuth()
  const { isAdmin, canViewKillSwitch } = useRole()
  const navRef    = useRef(null)
  const [unreadCount, setUnreadCount] = useState(0)

  const fetchUnread = useCallback(async () => {
    if (!isAuthenticated) return
    try {
      const res = await notificationService.getCount()
      setUnreadCount((res?.data?.unread ?? res?.unread ?? 0))
    } catch {}
  }, [isAuthenticated])

  useEffect(() => {
    fetchUnread()
    const id = setInterval(fetchUnread, 60_000)
    return () => clearInterval(id)
  }, [fetchUnread])

  const advancedActive = advancedNav.some((i) => location.pathname.startsWith(i.path))
  const [advancedOpen, setAdvancedOpen] = useState(advancedActive)
  useEffect(() => { if (advancedActive) setAdvancedOpen(true) }, [advancedActive])

  const admin = canViewKillSwitch ? [...adminNav, killSwitchItem] : adminNav
  const adminActive = admin.some((i) => location.pathname.startsWith(i.path))
  const [adminOpen, setAdminOpen] = useState(adminActive)
  useEffect(() => { if (adminActive) setAdminOpen(true) }, [adminActive])

  // Keyboard navigation inside sidebar — j/k cycles through visible items.
  useEffect(() => {
    if (!navRef.current) return
    const handler = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
      const links = navRef.current.querySelectorAll('a[href]')
      const idx = Array.from(links).findIndex((l) => l === document.activeElement)
      if (e.key === 'j' && idx < links.length - 1) links[idx + 1]?.focus()
      if (e.key === 'k' && idx > 0)                links[idx - 1]?.focus()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const renderItem = (item) => (
    <NavLink
      key={item.path}
      to={item.path}
      onClick={onClose}
      className={({ isActive }) =>
        'group flex items-center gap-3 px-3 py-2 rounded-md text-xs transition-colors ' +
        (isActive
          ? 'bg-white/[0.07] text-white border border-white/[0.07]'
          : 'text-neutral-400 hover:text-white hover:bg-white/[0.03]') +
        (item.danger ? ' hover:border-red-500/40' : '')
      }
    >
      <item.icon size={14} aria-hidden="true" />
      <span className="flex-1 truncate">{item.label}</span>
      {item.hint && (
        <kbd className="text-[9px] uppercase tracking-widest text-neutral-600 font-mono">
          {item.hint}
        </kbd>
      )}
    </NavLink>
  )

  return (
    <aside
      className={
        'fixed inset-y-0 left-0 w-64 bg-[#040404] border-r border-white/[0.06] z-30 ' +
        'transform transition-transform duration-200 lg:translate-x-0 lg:static ' +
        (isOpen ? 'translate-x-0' : '-translate-x-full')
      }
      aria-label="Main navigation"
    >
      <div className="flex flex-col h-full">
        <div className="px-4 py-4 flex items-center justify-between border-b border-white/[0.06]">
          <NavLink to="/dashboard" onClick={onClose} className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-white text-black flex items-center justify-center">
              <Shield size={14} aria-hidden="true" />
            </div>
            <span className="text-sm font-bold text-white tracking-tight">AgentControl</span>
          </NavLink>
          <button
            type="button"
            onClick={onClose}
            className="lg:hidden text-neutral-400 hover:text-white"
            aria-label="Close navigation"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="px-3 py-3 border-b border-white/[0.06]">
          <AgentScopePicker />
        </div>

        <nav ref={navRef} className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
          {primaryNav.map(renderItem)}

          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="w-full mt-3 mb-1 px-3 py-1.5 flex items-center justify-between text-[10px] uppercase tracking-widest text-neutral-500 hover:text-neutral-300 transition-colors"
          >
            <span>Advanced</span>
            {advancedOpen
              ? <ChevronDown size={11} aria-hidden="true" />
              : <ChevronRight size={11} aria-hidden="true" />}
          </button>
          {advancedOpen && advancedNav.map(renderItem)}

          {isAdmin && (
            <>
              <button
                type="button"
                onClick={() => setAdminOpen((v) => !v)}
                className="w-full mt-3 mb-1 px-3 py-1.5 flex items-center justify-between text-[10px] uppercase tracking-widest text-neutral-500 hover:text-neutral-300 transition-colors"
              >
                <span>Admin</span>
                {adminOpen
                  ? <ChevronDown size={11} aria-hidden="true" />
                  : <ChevronRight size={11} aria-hidden="true" />}
              </button>
              {adminOpen && admin.map(renderItem)}
            </>
          )}
        </nav>

        <div className="border-t border-white/[0.06] px-3 py-3 space-y-2">
          <NavLink
            to="/notifications"
            onClick={onClose}
            className="group flex items-center gap-3 px-3 py-2 rounded-md text-xs text-neutral-400 hover:text-white hover:bg-white/[0.03] transition-colors"
          >
            <Bell size={14} aria-hidden="true" />
            <span className="flex-1 truncate">Notifications</span>
            {unreadCount > 0 && (
              <span className="bg-red-500 text-white text-[9px] font-bold rounded-full px-1.5 py-0.5">
                {unreadCount > 99 ? '99+' : unreadCount}
              </span>
            )}
          </NavLink>
          <button
            type="button"
            onClick={async () => { await authService.logout(); updateAuth({ isAuthenticated: false }); navigate('/login') }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-xs text-neutral-400 hover:text-white hover:bg-white/[0.03] transition-colors"
          >
            <LogOut size={14} aria-hidden="true" />
            Sign out
          </button>
        </div>
      </div>
    </aside>
  )
}
