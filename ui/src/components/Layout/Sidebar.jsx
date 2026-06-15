import React, { useEffect, useRef, useState, useCallback } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  Users, Shield, FileText, X, Power, Zap,
  LogOut, Terminal, BarChart2,
  GitMerge, AlertTriangle, Crosshair, Bot,
  Network, Film, ShieldCheck, ChevronDown, ChevronRight, Settings as SettingsIcon,
  CreditCard, Radio, Bell, BookOpen, Github,
  Workflow, MessagesSquare, Gauge, HeartPulse, DollarSign, Share2,
  Beaker, EyeOff, Inbox, Sparkles,
} from 'lucide-react'
import { authService, notificationService } from '../../services/api'
import { useAuth } from '../../hooks/useAuth'
import { useRole } from '../../hooks/useRole'
import AgentScopePicker from './AgentScopePicker'

// Primary nav — the 5 items every buyer should see first.
// Anchored on the wedge: tamper-evident replay + runtime deny.
// `hint` is the keyboard shortcut surfaced in the sidebar — kept in sync with
// the bindings registered in App.jsx:<GlobalShortcuts>.
const primaryNav = [
  { path: '/live-demo',       label: 'Live Demo',       icon: Sparkles,     hint: 'G X' },
  { path: '/fleet',           label: 'Fleet',           icon: Gauge,        hint: 'G H' },
  { path: '/flight-recorder', label: 'Flight Recorder', icon: Film,         hint: 'G F' },
  { path: '/policy-builder',  label: 'Policies',        icon: GitMerge,     hint: 'G P' },
  { path: '/audit-logs',      label: 'Audit Trail',     icon: BarChart2,    hint: 'G A' },
  { path: '/incidents',       label: 'Incidents',       icon: AlertTriangle,hint: 'G I' },
  { path: '/settings',        label: 'Settings',        icon: SettingsIcon, hint: 'G S' },
]

// Secondary nav — power-user operations, collapsed by default.
const operationsNav = [
  { path: '/agents',          label: 'Agents',           icon: Users       },
  // Sprint 3 — Decision + Session Explorer
  { path: '/decision-explorer', label: 'Decision Explorer', icon: Workflow,        hint: 'G D' },
  { path: '/session-explorer',  label: 'Session Explorer',  icon: MessagesSquare,  hint: 'G E' },
  // Sprint 4 — Agent FinOps + topology
  { path: '/agent-health',    label: 'Agent Health',     icon: HeartPulse  },
  { path: '/agent-cost',      label: 'Agent FinOps',     icon: DollarSign  },
  { path: '/agent-topology',  label: 'Agent Topology',   icon: Share2      },
  // Sprint 5 — Attack Evaluation Suite
  { path: '/evaluation',      label: 'Evaluation',       icon: Beaker      },
  // Sprint 6 — Shadow Mode (legacy analytics)
  { path: '/shadow-mode',     label: 'Shadow Mode',      icon: EyeOff      },
  // Sprint 3 — Shadow Mode Review (owner-facing review feed for the
  // 14-day default observe-only window every new workspace starts in).
  { path: '/shadow-review',   label: 'Shadow Review',    icon: ShieldCheck },
  // Sprint 7 — Policy Playground (replay history under a draft policy)
  { path: '/policy-playground', label: 'Policy Replay',  icon: Beaker      },
  { path: '/identity-graph',  label: 'Identity Graph',   icon: Network,    hint: 'G G' },
  { path: '/autonomy',        label: 'Autonomy',         icon: ShieldCheck },
  { path: '/approval-inbox',  label: 'Approval Inbox',   icon: Inbox       },
  { path: '/forensics',       label: 'Forensics',        icon: FileText    },
  { path: '/playground',      label: 'Agent Sandbox',    icon: Terminal    },
  { path: '/live-feed',        label: 'Live Feed',         icon: Radio,      hint: 'G L' },
  { path: '/playbooks',       label: 'Playbooks',         icon: BookOpen    },
  { path: '/auto-response',   label: 'Auto Response',    icon: Bot         },
  { path: '/compliance',      label: 'Compliance',       icon: Shield      },
  { path: '/open-source',     label: 'Open Source',      icon: Github      },
  { path: '/attack-sim',      label: 'Attack Sim',       icon: Crosshair   },
]

const killSwitchItem = { path: '/kill-switch', label: 'Kill Switch', icon: Power, danger: true }

export default function Sidebar({ isOpen, onClose }) {
  const location  = useLocation()
  const navigate  = useNavigate()
  const { updateAuth, isAuthenticated } = useAuth()
  const { canViewKillSwitch } = useRole()
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

  // Auto-expand Operations group if user is on a secondary page
  const onOpsPage = operationsNav.some((i) => location.pathname.startsWith(i.path))
                  || (canViewKillSwitch && location.pathname.startsWith(killSwitchItem.path))
  const [opsOpen, setOpsOpen] = useState(onOpsPage)
  useEffect(() => { if (onOpsPage) setOpsOpen(true) }, [onOpsPage])

  const ops = canViewKillSwitch ? [...operationsNav, killSwitchItem] : operationsNav

  // Keyboard navigation inside sidebar
  useEffect(() => {
    if (!isOpen) return
    const handleKey = (e) => {
      if (e.key !== 'Tab') return
      const focusables = navRef.current?.querySelectorAll('a, button')
      if (!focusables?.length) return
      const first = focusables[0]
      const last  = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [isOpen])

  const handleLogout = async () => {
    try { await authService.logout() } catch {}
    updateAuth({ isAuthenticated: false, user: null, tenant_id: null, token: null })
    navigate('/login')
  }

  const renderItem = (item) => {
    const isActive = location.pathname === item.path ||
      (item.path !== '/' && location.pathname.startsWith(item.path))
    return (
      <NavLink
        key={item.path}
        to={item.path}
        onClick={() => window.innerWidth < 1024 && onClose()}
        aria-current={isActive ? 'page' : undefined}
        className={`
          flex items-center gap-2.5 px-3 py-2.5 rounded-lg
          text-xs font-medium transition-all duration-150 outline-none
          focus-visible:ring-1 focus-visible:ring-white/30
          ${isActive
            ? item.danger
              ? 'bg-red-500/15 text-red-400 border border-red-500/20'
              : 'bg-white text-black shadow-sm'
            : item.danger
              ? 'text-neutral-500 hover:text-red-400 hover:bg-red-500/10 border border-transparent'
              : 'text-neutral-500 hover:text-white hover:bg-white/[0.05] border border-transparent hover:border-white/[0.06]'
          }
        `}
        style={isActive && !item.danger ? { boxShadow: '0 1px 8px rgba(255,255,255,0.12)' } : undefined}
      >
        <item.icon size={15} className="shrink-0" aria-hidden="true" />
        <span className="truncate flex-1">{item.label}</span>
        {item.hint && (
          <kbd
            className={`
              hidden lg:inline-flex items-center gap-0.5 shrink-0
              px-1.5 py-0.5 rounded
              text-[9px] font-mono font-semibold
              ${isActive
                ? 'bg-black/10 text-black/60'
                : 'bg-white/[0.04] text-neutral-600 group-hover:text-neutral-400'}
            `}
            aria-hidden="true"
          >
            {item.hint}
          </kbd>
        )}
      </NavLink>
    )
  }

  return (
    <>
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/75 lg:hidden z-40 backdrop-blur-sm transition-opacity"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/*
        z-index contract (must match Modal + MainLayout):
          - mobile (drawer):  aside z-50 (above the z-40 backdrop), but
                              still BELOW Modal because modals open in a
                              portal at z-50 overlay / z-[60] content.
          - desktop (static): z-30 — sits within the document flow so the
                              navbar (z-40) overlays it. This prevents the
                              sidebar from punching above modals or toasts.
      */}
      <aside
        ref={navRef}
        className={`
          fixed inset-y-0 left-0 z-50
          lg:static lg:z-30
          w-64 flex flex-col
          bg-[var(--bg-surface)]
          border-r border-[var(--border-subtle)]
          transition-transform duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]
          motion-reduce:transition-none
          ${isOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
        `}
        aria-label="Main navigation"
      >
        {/* Brand */}
        <div className="h-14 px-5 flex items-center justify-between border-b border-[var(--border-subtle)] shrink-0">
          <div className="flex items-center gap-2.5">
            <div
              className="w-7 h-7 rounded-md bg-white flex items-center justify-center shrink-0"
              style={{ boxShadow: '0 0 12px rgba(255,255,255,0.15)' }}
            >
              <Shield className="text-black" size={15} />
            </div>
            <span className="text-xs font-bold tracking-tight text-white font-mono">AgentControl</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            className="lg:hidden p-1.5 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.06] transition-colors"
          >
            <X size={17} />
          </button>
        </div>

        {/* Wedge tagline */}
        <div className="px-5 py-3 border-b border-[var(--border-subtle)] shrink-0">
          <p className="text-[10px] leading-snug text-neutral-500 font-mono">
            Tamper-evident replay + runtime deny<br />for AI agents.
          </p>
        </div>

        {/* Agent scope picker — shared with Topbar via AgentScopePicker */}
        <div className="px-3 py-2.5 border-b border-[var(--border-subtle)] shrink-0">
          <div className="flex items-center gap-2">
            <Users size={11} className="text-neutral-500 shrink-0" aria-hidden="true" />
            <span className="text-[10px] uppercase tracking-wider text-neutral-500 shrink-0">Scope:</span>
            <AgentScopePicker variant="compact" />
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto" aria-label="Application pages">
          {primaryNav.map(renderItem)}

          <div className="pt-4 pb-1">
            <button
              type="button"
              onClick={() => setOpsOpen((v) => !v)}
              className="flex items-center gap-1.5 w-full px-3 py-1.5 text-[10px] uppercase tracking-wider text-neutral-600 hover:text-neutral-400 transition-colors"
              aria-expanded={opsOpen}
            >
              {opsOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <span>Operations</span>
            </button>
          </div>
          {opsOpen && ops.map(renderItem)}
        </nav>

        {/* Footer */}
        <div className="px-3 py-4 border-t border-[var(--border-subtle)] space-y-1 shrink-0">
          <button
            onClick={() => { navigate('/notifications'); window.innerWidth < 1024 && onClose() }}
            className="flex items-center gap-2.5 w-full px-3 py-2.5 rounded-lg text-xs font-medium text-neutral-500 hover:text-white hover:bg-white/[0.05] transition-all duration-150"
            aria-label={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
          >
            <div className="relative shrink-0">
              <Bell size={15} aria-hidden="true" />
              {unreadCount > 0 && (
                <span className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-white text-black text-[9px] font-bold flex items-center justify-center leading-none">
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </div>
            <span>Notifications</span>
          </button>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2.5 w-full px-3 py-2.5 rounded-lg text-xs font-medium text-neutral-500 hover:text-white hover:bg-red-500/10 transition-all duration-150"
          >
            <LogOut size={15} className="shrink-0" aria-hidden="true" />
            <span>Sign Out</span>
          </button>
          <div className="flex items-center gap-2.5 px-3 py-2">
            <div className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" aria-hidden="true" />
            <span className="text-xs text-neutral-600 font-mono">v4.4.0</span>
          </div>
        </div>
      </aside>
    </>
  )
}
