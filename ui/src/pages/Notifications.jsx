import React, { useEffect, useState, useCallback } from 'react'
import {
  Bell, Check, CheckCheck, RefreshCw, Info,
  AlertTriangle, CheckCircle2, XCircle, Loader2,
  ExternalLink, Filter,
} from 'lucide-react'
import { notificationService } from '../services/api'

const LEVEL_CONFIG = {
  info:    { icon: Info,          color: 'text-blue-400',   bg: 'bg-blue-500/10',   border: 'border-blue-500/20' },
  warning: { icon: AlertTriangle, color: 'text-amber-400',  bg: 'bg-amber-500/10',  border: 'border-amber-500/20' },
  error:   { icon: XCircle,       color: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/20' },
  success: { icon: CheckCircle2,  color: 'text-green-400',  bg: 'bg-green-500/10',  border: 'border-green-500/20' },
}

const CATEGORY_LABELS = {
  policy:   'Policy',
  incident: 'Incident',
  quota:    'Quota',
  system:   'System',
}

function NotificationItem({ notif, onRead }) {
  const cfg  = LEVEL_CONFIG[notif.level] || LEVEL_CONFIG.info
  const Icon = cfg.icon
  const isUnread = !notif.is_read

  return (
    <div
      className={`relative flex gap-3 p-4 border-b border-[var(--border-subtle)] last:border-0 transition-colors hover:bg-white/[0.02] ${isUnread ? 'bg-white/[0.01]' : ''}`}
    >
      {isUnread && <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-white/30 rounded-r" />}
      <div className={`shrink-0 w-8 h-8 rounded-lg ${cfg.bg} border ${cfg.border} flex items-center justify-center`}>
        <Icon size={14} className={cfg.color} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div>
            <span className="text-sm font-medium text-white">{notif.title}</span>
            {notif.category && (
              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded-full bg-white/[0.06] text-neutral-500">
                {CATEGORY_LABELS[notif.category] || notif.category}
              </span>
            )}
          </div>
          <span className="text-[10px] text-neutral-600 shrink-0">
            {new Date(notif.created_at).toLocaleString()}
          </span>
        </div>
        <p className="text-xs text-neutral-500 mt-0.5">{notif.body}</p>
        <div className="flex items-center gap-3 mt-2">
          {notif.link && (
            <a href={notif.link} className="flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
              <ExternalLink size={11} /> View
            </a>
          )}
          {isUnread && (
            <button onClick={() => onRead(notif.id)} className="flex items-center gap-1 text-xs text-neutral-600 hover:text-white">
              <Check size={11} /> Mark read
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Notifications() {
  const [notifications, setNotifications] = useState([])
  const [loading, setLoading]   = useState(true)
  const [markingAll, setMarkingAll] = useState(false)
  const [filter, setFilter]     = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [totalUnread, setTotalUnread] = useState(null)

  const fetchCount = useCallback(async () => {
    try {
      const res = await notificationService.getCount()
      const count = res?.data?.count ?? res?.count ?? null
      if (count !== null) setTotalUnread(count)
    } catch {}
  }, [])

  const load = useCallback(async () => {
    try {
      const params = filter === 'unread' ? { unread_only: true, limit: 50 } : { limit: 50 }
      const res = await notificationService.list(params)
      setNotifications(res?.data || res || [])
    } catch { setNotifications([]) }
    setLoading(false)
  }, [filter])

  useEffect(() => { load() }, [load])
  useEffect(() => { fetchCount() }, [fetchCount])

  const handleRead = async (id) => {
    await notificationService.markRead(id).catch(() => {})
    setNotifications(ns => ns.map(n => n.id === id ? { ...n, is_read: true } : n))
    fetchCount()
  }

  const handleMarkAll = async () => {
    setMarkingAll(true)
    await notificationService.markAllRead().catch(() => {})
    setNotifications(ns => ns.map(n => ({ ...n, is_read: true })))
    setTotalUnread(0)
    setMarkingAll(false)
  }

  const derivedUnread = notifications.filter(n => !n.is_read).length
  const unreadCount = totalUnread ?? derivedUnread
  const categories  = ['all', ...new Set(notifications.map(n => n.category).filter(Boolean))]

  const visible = notifications.filter(n => {
    if (filter === 'unread' && n.is_read) return false
    if (categoryFilter !== 'all' && n.category !== categoryFilter) return false
    return true
  })

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-white mb-1">Notifications</h1>
            <p className="text-sm text-neutral-400">System alerts, policy events, and quota warnings.</p>
          </div>
          {unreadCount > 0 && (
            <span className="flex items-center justify-center w-6 h-6 rounded-full bg-white text-black text-xs font-bold">
              {unreadCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20">
            <RefreshCw size={12} /> Refresh
          </button>
          {unreadCount > 0 && (
            <button
              onClick={handleMarkAll}
              disabled={markingAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200 disabled:opacity-50"
            >
              {markingAll ? <Loader2 size={12} className="animate-spin" /> : <CheckCheck size={12} />}
              Mark all read
            </button>
          )}
        </div>
      </header>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1.5">
          {['all', 'unread'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-all ${filter === f ? 'border-white/30 bg-white/[0.08] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
            >
              {f === 'all' ? 'All' : 'Unread'}{f === 'unread' && unreadCount > 0 ? ` (${unreadCount})` : ''}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 text-neutral-600">
          <Filter size={11} />
        </div>
        <div className="flex gap-1.5">
          {categories.map(c => (
            <button
              key={c}
              onClick={() => setCategoryFilter(c)}
              className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all ${categoryFilter === c ? 'border-white/20 bg-white/[0.05] text-white' : 'border-[var(--border-subtle)] text-neutral-600 hover:border-white/10'}`}
            >
              {c === 'all' ? 'All types' : CATEGORY_LABELS[c] || c}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-32">
          <Loader2 className="animate-spin text-neutral-500" size={24} />
        </div>
      ) : visible.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16">
          <div className="w-12 h-12 rounded-xl bg-white/[0.04] flex items-center justify-center mb-4">
            <Bell size={20} className="text-neutral-600" />
          </div>
          <div className="text-sm text-neutral-500">
            {filter === 'unread' ? 'No unread notifications.' : 'No notifications yet.'}
          </div>
        </div>
      ) : (
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
          {visible.map(n => (
            <NotificationItem key={n.id} notif={n} onRead={handleRead} />
          ))}
        </div>
      )}
    </div>
  )
}
