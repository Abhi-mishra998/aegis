import React, { useEffect, useState, useCallback, useContext, useMemo, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  Bell, Check, CheckCheck, RefreshCw, Info,
  AlertTriangle, CheckCircle2, XCircle, Loader2,
  ExternalLink, Filter, Radio, ArrowRight, Wifi, WifiOff,
} from 'lucide-react'
import { notificationService } from '../services/api'
import { AuthContext } from '../context/AuthContext'
import { useSSE } from '../hooks/useSSE'
import SkeletonLoader from '../components/Common/SkeletonLoader'

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

// localStorage key used to persist the operator's read-state acks even if
// the backend roundtrip fails. The Notifications page POSTs to /notifications/{id}/read
// on every Mark-read click, but on flaky networks we still want the UI to
// reflect the operator's intent across page reloads — otherwise they'll
// re-ack the same row every refresh.
const READ_LS_KEY = 'aegis_notifications_read_v1'
const READ_LS_MAX = 500

function loadReadCache() {
  try {
    const raw = localStorage.getItem(READ_LS_KEY)
    if (!raw) return new Set()
    const arr = JSON.parse(raw)
    return new Set(Array.isArray(arr) ? arr : [])
  } catch {
    return new Set()
  }
}

function saveReadCache(set) {
  try {
    // Cap at READ_LS_MAX entries to bound storage growth. Drop oldest by
    // insertion order (Set iteration is insertion-ordered in JS).
    const arr = Array.from(set)
    const trimmed = arr.slice(-READ_LS_MAX)
    localStorage.setItem(READ_LS_KEY, JSON.stringify(trimmed))
  } catch {
    /* quota exceeded / private mode — silently ignore */
  }
}

function NotificationItem({ notif, onRead }) {
  const cfg  = LEVEL_CONFIG[notif.level] || LEVEL_CONFIG.info
  const Icon = cfg.icon
  const isUnread = !notif.is_read

  return (
    <div
      className={`relative flex gap-3 p-4 border-b border-[var(--border-subtle)] last:border-0 transition-colors hover:bg-white/[0.02] ${isUnread ? 'bg-white/[0.01]' : ''}`}
    >
      {isUnread && <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-white/30 rounded-r" aria-hidden="true" />}
      <div className={`shrink-0 w-8 h-8 rounded-lg ${cfg.bg} border ${cfg.border} flex items-center justify-center`}>
        <Icon size={14} className={cfg.color} aria-hidden="true" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <span className="text-sm font-medium text-white">{notif.title}</span>
            {notif.category && (
              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded-full bg-white/[0.06] text-neutral-500">
                {CATEGORY_LABELS[notif.category] || notif.category}
              </span>
            )}
            {isUnread && (
              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/15 text-red-300 border border-red-500/20 uppercase tracking-wider">
                New
              </span>
            )}
          </div>
          <span className="text-[10px] text-neutral-600 shrink-0">
            {new Date(notif.created_at).toLocaleString()}
          </span>
        </div>
        <p className="text-xs text-neutral-500 mt-0.5 break-words">{notif.body}</p>
        <div className="flex items-center gap-3 mt-2">
          {notif.link && (
            <a href={notif.link} className="flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
              <ExternalLink size={11} aria-hidden="true" /> View
            </a>
          )}
          {isUnread && (
            <button onClick={() => onRead(notif.id)} className="flex items-center gap-1 text-xs text-neutral-600 hover:text-white">
              <Check size={11} aria-hidden="true" /> Mark read
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Notifications() {
  const { addToast } = useContext(AuthContext)
  const [notifications, setNotifications] = useState([])
  const [loading, setLoading]   = useState(true)
  const [markingAll, setMarkingAll] = useState(false)
  const [filter, setFilter]     = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [totalUnread, setTotalUnread] = useState(null)
  // Local read-state cache survives reloads even when the backend write
  // path is in-flight or failed. Hydrated from localStorage on mount.
  const readCacheRef = useRef(loadReadCache())

  // Apply the local read-cache overlay so a Mark-read action that didn't
  // round-trip back to /notifications still wins on rehydration.
  const applyReadOverlay = useCallback((items) => {
    if (!Array.isArray(items)) return []
    const cache = readCacheRef.current
    return items.map((n) => (cache.has(n.id) ? { ...n, is_read: true } : n))
  }, [])

  const fetchCount = useCallback(async () => {
    try {
      const res = await notificationService.getCount()
      const count = res?.data?.count ?? res?.count ?? null
      if (count !== null) setTotalUnread(count)
    } catch { /* count is best-effort */ }
  }, [])

  const load = useCallback(async () => {
    try {
      const params = filter === 'unread' ? { unread_only: true, limit: 50 } : { limit: 50 }
      const res = await notificationService.list(params)
      const items = Array.isArray(res?.data) ? res.data : Array.isArray(res) ? res : (res?.data?.items || res?.items || [])
      setNotifications(applyReadOverlay(items))
    } catch { setNotifications([]) }
    setLoading(false)
  }, [filter, applyReadOverlay])

  useEffect(() => { load() }, [load])
  useEffect(() => { fetchCount() }, [fetchCount])

  // Real-time SSE: when the gateway publishes a new alert/policy/quota
  // event that maps to a notification, refetch so the page mirrors the
  // server without the operator having to click Refresh. We deliberately
  // refetch (not local merge) because notification rows can carry server-
  // assigned ids and persistence guarantees we don't get from the SSE
  // payload alone.
  //
  // Debounced — back-to-back alerts during an incident burst would
  // otherwise hammer /notifications/* every few ms.
  const refetchTimerRef = useRef(null)
  const scheduleRefetch = useCallback(() => {
    if (refetchTimerRef.current) return
    refetchTimerRef.current = setTimeout(() => {
      refetchTimerRef.current = null
      load()
      fetchCount()
    }, 800)
  }, [load, fetchCount])
  useEffect(() => () => {
    if (refetchTimerRef.current) clearTimeout(refetchTimerRef.current)
  }, [])

  // SSE channels we listen for. The backend doesn't (yet) publish a
  // dedicated `notification_created` event, so we subscribe to the four
  // event types that historically write a notification row server-side.
  // If a future backend revision adds a dedicated channel, drop the
  // others — they're cheap subscriptions either way.
  const sseChannels = useMemo(() => ({
    alert:           () => scheduleRefetch(),
    policy_decision: () => scheduleRefetch(),
    quota_warning:   () => scheduleRefetch(),
    kill_switch:     () => scheduleRefetch(),
    notification:    () => scheduleRefetch(),
  }), [scheduleRefetch])

  const { state: sseState, lastError: sseError } = useSSE({
    onMessage: scheduleRefetch,
    channels:  sseChannels,
  })

  const handleRead = async (id) => {
    // Optimistic: mark read locally + persist to cache first so the UI is
    // snappy and the ack survives a reload even if /notifications/{id}/read
    // hasn't returned yet.
    readCacheRef.current.add(id)
    saveReadCache(readCacheRef.current)
    setNotifications(ns => ns.map(n => n.id === id ? { ...n, is_read: true } : n))
    try {
      await notificationService.markRead(id)
      fetchCount()
    } catch (err) {
      addToast?.(`Failed to mark notification as read: ${err?.message || 'unknown error'}`, 'error')
    }
  }

  const handleMarkAll = async () => {
    setMarkingAll(true)
    // Optimistic local update + cache write so a network failure on
    // /notifications/read-all still leaves the UI in the operator's
    // intended state.
    const unreadIds = notifications.filter(n => !n.is_read).map(n => n.id)
    for (const id of unreadIds) readCacheRef.current.add(id)
    saveReadCache(readCacheRef.current)
    setNotifications(ns => ns.map(n => ({ ...n, is_read: true })))
    setTotalUnread(0)
    try {
      await notificationService.markAllRead()
    } catch (err) {
      addToast?.(`Failed to mark all as read: ${err?.message || 'unknown error'}`, 'error')
    } finally {
      setMarkingAll(false)
    }
  }

  const derivedUnread = notifications.filter(n => !n.is_read).length
  const unreadCount = totalUnread ?? derivedUnread
  const categories  = ['all', ...Array.from(new Set(notifications.map(n => n.category).filter(Boolean)))]

  // Dedup by id — protects us from a race where load() and the SSE-driven
  // refetch land overlapping pages of the same rows, which used to render
  // the same row twice with two different React keys.
  const visible = useMemo(() => {
    const seen = new Set()
    return notifications.filter((n) => {
      if (!n || seen.has(n.id)) return false
      seen.add(n.id)
      if (filter === 'unread' && n.is_read) return false
      if (categoryFilter !== 'all' && n.category !== categoryFilter) return false
      return true
    })
  }, [notifications, filter, categoryFilter])

  return (
    // Container widened from max-w-3xl (768px — wastes >65% at 1920px) to
    // max-w-5xl with responsive padding so the page works on monitor walls
    // without leaving the operator squinting at a strip of text.
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 space-y-6">
      <header className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-white mb-1 flex items-center gap-2">
              <Bell size={18} className="text-neutral-500" aria-hidden="true" />
              Notifications
            </h1>
            <p className="text-sm text-neutral-400">System alerts, policy events, and quota warnings.</p>
          </div>
          {unreadCount > 0 && (
            <span
              className="flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-full bg-white text-black text-xs font-bold"
              aria-label={`${unreadCount} unread notifications`}
            >
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* SSE state pill — gives operators a tell that real-time
              updates are wired (vs. silent failure). */}
          <span
            className={`flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border ${
              sseState === 'open'
                ? 'bg-green-500/10 text-green-300 border-green-500/30'
                : sseState === 'connecting'
                  ? 'bg-amber-500/10 text-amber-300 border-amber-500/30'
                  : 'bg-neutral-500/10 text-neutral-400 border-neutral-500/30'
            }`}
            title={sseState === 'open' ? 'Real-time updates active' : `SSE ${sseState}${sseError ? ` — ${sseError}` : ''}`}
          >
            {sseState === 'open'
              ? <><Wifi size={11} aria-hidden="true" /> Live</>
              : sseState === 'connecting'
                ? <><Loader2 size={11} className="animate-spin" aria-hidden="true" /> Connecting</>
                : <><WifiOff size={11} aria-hidden="true" /> Offline</>
            }
          </span>
          <button
            onClick={load}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20"
            aria-label="Refresh notifications"
          >
            <RefreshCw size={12} aria-hidden="true" /> Refresh
          </button>
          {unreadCount > 0 && (
            <button
              onClick={handleMarkAll}
              disabled={markingAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200 disabled:opacity-50"
              aria-label="Mark all notifications as read"
            >
              {markingAll
                ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                : <CheckCheck size={12} aria-hidden="true" />}
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
              aria-pressed={filter === f}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-all ${filter === f ? 'border-white/30 bg-white/[0.08] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
            >
              {f === 'all' ? 'All' : 'Unread'}{f === 'unread' && unreadCount > 0 ? ` (${unreadCount})` : ''}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 text-neutral-600" aria-hidden="true">
          <Filter size={11} />
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {categories.map(c => (
            <button
              key={c}
              onClick={() => setCategoryFilter(c)}
              aria-pressed={categoryFilter === c}
              className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all ${categoryFilter === c ? 'border-white/20 bg-white/[0.05] text-white' : 'border-[var(--border-subtle)] text-neutral-600 hover:border-white/10'}`}
            >
              {c === 'all' ? 'All types' : CATEGORY_LABELS[c] || c}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        // Skeleton rows — same visual rhythm as the live list, no fake data.
        <div
          className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden"
          role="status"
          aria-label="Loading notifications"
        >
          <SkeletonLoader variant="row" count={5} />
        </div>
      ) : visible.length === 0 ? (
        // Actionable empty state with CTAs. Different copy + CTA for
        // unread-filter-empty vs no-notifications-at-all.
        <div className="flex flex-col items-center justify-center py-16 px-6 gap-4 text-center bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl">
          <div className="w-12 h-12 rounded-xl bg-white/[0.04] flex items-center justify-center">
            <Bell size={22} className="text-neutral-600" aria-hidden="true" />
          </div>
          {filter === 'unread' ? (
            <>
              <div>
                <p className="text-sm text-neutral-200 font-medium">Inbox zero.</p>
                <p className="text-xs text-neutral-500 mt-1">
                  No unread notifications. Switch to All to view history.
                </p>
              </div>
              <button
                onClick={() => setFilter('all')}
                className="text-xs px-3 py-1.5 rounded-lg border border-white/15 text-neutral-200 hover:bg-white/[0.05]"
              >
                Show all notifications
              </button>
            </>
          ) : (
            <>
              <div>
                <p className="text-sm text-neutral-200 font-medium">No notifications yet.</p>
                <p className="text-xs text-neutral-500 mt-1 max-w-md">
                  Policy decisions, quota warnings, and kill-switch events will land here as your
                  agents start making calls.
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap justify-center">
                <Link
                  to="/live-feed"
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-white text-black font-medium hover:bg-neutral-200"
                >
                  <Radio size={12} aria-hidden="true" /> Open Live Feed <ArrowRight size={11} aria-hidden="true" />
                </Link>
                <Link
                  to="/agents"
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-white/15 text-neutral-200 hover:bg-white/[0.05]"
                >
                  Register an agent
                </Link>
              </div>
            </>
          )}
        </div>
      ) : (
        <div
          className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden"
          aria-live="polite"
          aria-relevant="additions"
        >
          <div className="px-4 py-2 border-b border-[var(--border-subtle)] flex items-center justify-between">
            <span className="text-[10px] text-neutral-600">
              {visible.length} notification{visible.length === 1 ? '' : 's'}
            </span>
            <span className="text-[10px] text-neutral-700 font-mono">newest first</span>
          </div>
          {visible.map(n => (
            <NotificationItem key={n.id} notif={n} onRead={handleRead} />
          ))}
        </div>
      )}
    </div>
  )
}
