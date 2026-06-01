import React, { useState, useMemo, useEffect, useRef, useContext, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import { AgentContext } from '../../context/AgentContext'
import { authService, incidentService } from '../../services/api'
import { LogOut, Menu, ChevronDown, Settings, User, Zap, Bot, Command, AlertTriangle } from 'lucide-react'
import NotificationCenter from '../Common/NotificationCenter'
import AgentScopePicker from './AgentScopePicker'
import VoiceAgentButton from '../VoiceAgent/VoiceAgentButton'

export default function Topbar({ onMenuClick, onCommandPalette }) {
  const navigate  = useNavigate()
  const { user, tenant_id, updateAuth, addToast } = useAuth()
  const { sseConnected } = useContext(AgentContext)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [openIncidents, setOpenIncidents] = useState(0)
  const dropdownRef = useRef(null)

  const fetchIncidentCount = useCallback(async () => {
    try {
      const res = await incidentService.getSummary()
      const s = res?.data || res || {}
      setOpenIncidents((s.open ?? 0) + (s.investigating ?? 0))
    } catch {}
  }, [])

  useEffect(() => {
    fetchIncidentCount()
    const id = setInterval(fetchIncidentCount, 60_000)
    return () => clearInterval(id)
  }, [fetchIncidentCount])

  const role = useMemo(() => {
    const stored = localStorage.getItem('user_role')
    return stored ? stored.toUpperCase() : 'VIEWER'
  }, [user])

  useEffect(() => {
    if (!dropdownOpen) return
    const handle = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setDropdownOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [dropdownOpen])

  useEffect(() => {
    if (!dropdownOpen) return
    const handle = (e) => { if (e.key === 'Escape') setDropdownOpen(false) }
    document.addEventListener('keydown', handle)
    return () => document.removeEventListener('keydown', handle)
  }, [dropdownOpen])

  const handleLogout = async () => {
    setDropdownOpen(false)
    try { await authService.logout() } catch {}
    updateAuth({ isAuthenticated: false, user: null, tenant_id: null, token: null })
    navigate('/login')
  }

  return (
    <header
      className="h-14 px-4 lg:px-6 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-center justify-between shrink-0 z-40"
      role="banner"
    >
      {/* Left: hamburger + tenant context */}
      <div className="flex items-center gap-4 min-w-0">
        <button
          onClick={onMenuClick}
          aria-label="Toggle navigation menu"
          className="lg:hidden p-2 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.06] transition-colors"
        >
          <Menu size={18} aria-hidden="true" />
        </button>

        <div className="flex flex-col min-w-0">
          <span className="text-label leading-none truncate">Security Node</span>
          <span className="text-xs font-semibold text-neutral-300 font-mono leading-tight mt-0.5 truncate">
            {tenant_id?.slice(0, 12) ?? 'Global Core'}
          </span>
        </div>
      </div>

      {/* Center: agent selector — shared with Sidebar via AgentScopePicker */}
      <div className="flex items-center gap-2 px-2">
        <Bot size={13} className="text-neutral-500 shrink-0" aria-hidden="true" />
        <AgentScopePicker variant="header" />
      </div>

      {/* Right: voice agent + SSE status + cmd palette + notifications + user menu */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Voice Agent — opens the killer animated overlay */}
        <VoiceAgentButton />

        {/* Live SSE indicator */}
        <div
          className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)]"
          aria-label={sseConnected ? 'Live stream active' : 'Reconnecting…'}
          title={sseConnected ? 'SSE connected' : 'SSE reconnecting…'}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${sseConnected ? 'bg-green-500' : 'bg-amber-500 animate-pulse'}`}
            aria-hidden="true"
            style={sseConnected ? { boxShadow: '0 0 6px rgba(34,197,94,0.5)' } : {}}
          />
          <span className="text-label leading-none">{sseConnected ? 'Live' : 'Syncing'}</span>
        </div>

        {/* Command palette trigger */}
        <button
          onClick={onCommandPalette}
          aria-label="Open command palette (Cmd+K)"
          className="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.06] border border-[var(--border-subtle)] hover:border-[var(--border-default)] transition-colors"
        >
          <Command size={13} aria-hidden="true" />
          <span className="text-xs font-mono hidden md:inline">⌘K</span>
        </button>

        {/* Open incidents badge */}
        <button
          onClick={() => navigate('/incidents')}
          aria-label={openIncidents > 0 ? `${openIncidents} open incidents` : 'Incidents — all clear'}
          title="Open incidents"
          className="relative p-2 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.06] transition-colors"
        >
          <AlertTriangle size={16} aria-hidden="true" className={openIncidents > 0 ? 'text-red-400' : ''} />
          {openIncidents > 0 && (
            <span
              className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-red-500 flex items-center justify-center text-[9px] font-bold text-white"
              aria-hidden="true"
              style={{ boxShadow: '0 0 8px rgba(239,68,68,0.6)' }}
            >
              {openIncidents > 9 ? '9+' : openIncidents}
            </span>
          )}
        </button>

        {/* Notification center */}
        <NotificationCenter />

        {/* User menu */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={dropdownOpen}
            className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/[0.05] transition-colors"
          >
            <div
              className="w-7 h-7 rounded-md bg-white flex items-center justify-center text-black font-bold text-xs shrink-0"
              aria-hidden="true"
            >
              {user?.charAt(0).toUpperCase() ?? 'U'}
            </div>
            <div className="hidden md:flex flex-col items-start min-w-0">
              <span className="text-xs font-semibold text-white truncate max-w-[120px]">{user ?? 'User'}</span>
              <span className="text-label leading-none">{role}</span>
            </div>
            <ChevronDown
              size={13}
              className={`text-neutral-500 transition-transform duration-200 ${dropdownOpen ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>

          {dropdownOpen && (
            <div
              role="menu"
              aria-label="User menu"
              className="
                absolute right-0 mt-2 w-60
                bg-[var(--bg-surface-elevated)]
                border border-[var(--border-default)]
                rounded-xl shadow-2xl
                overflow-hidden py-1.5
                animate-slide-down
              "
            >
              <div className="px-4 py-3 border-b border-[var(--border-subtle)]">
                <p className="text-label leading-none mb-1">Authenticated as</p>
                <p className="text-xs font-semibold text-white truncate mt-1">{user}</p>
                <div className="flex items-center gap-1.5 mt-1.5">
                  <Zap size={10} className="text-neutral-500" aria-hidden="true" />
                  <span className="text-label leading-none">{role}</span>
                </div>
              </div>

              <div className="p-1.5 space-y-0.5">
                <button
                  role="menuitem"
                  onClick={() => { setDropdownOpen(false); navigate('/settings') }}
                  className="w-full px-3 py-2 text-left text-xs font-medium text-neutral-400 hover:text-white hover:bg-white/[0.05] rounded-lg transition-colors flex items-center gap-2.5"
                >
                  <User size={14} aria-hidden="true" /> Profile
                </button>
                <button
                  role="menuitem"
                  onClick={() => { setDropdownOpen(false); navigate('/settings') }}
                  className="w-full px-3 py-2 text-left text-xs font-medium text-neutral-400 hover:text-white hover:bg-white/[0.05] rounded-lg transition-colors flex items-center gap-2.5"
                >
                  <Settings size={14} aria-hidden="true" /> Settings
                </button>
                <div className="border-t border-[var(--border-subtle)] my-1" role="separator" />
                <button
                  role="menuitem"
                  onClick={handleLogout}
                  className="w-full px-3 py-2 text-left text-xs font-medium text-red-400 hover:text-white hover:bg-red-500/15 rounded-lg transition-colors flex items-center gap-2.5"
                >
                  <LogOut size={14} aria-hidden="true" /> Sign Out
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
