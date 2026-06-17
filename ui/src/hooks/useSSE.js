import { useEffect, useRef, useCallback, useState } from 'react'

const API_BASE = import.meta.env.VITE_GATEWAY_URL || ''
const MAX_BACKOFF_MS = 32_000
const HEARTBEAT_TIMEOUT_MS = 45_000
const HEARTBEAT_WATCHDOG_INTERVAL_MS = 10_000
// Sprint 3: Clerk JWT rotation (60s TTL) can briefly disconnect SSE.
// If the session was clearly fresh (cookie expiry > 30s in the future) and
// the previous failure wasn't an auth_expired one, reconnect quickly instead
// of waiting through the exponential backoff so the UI doesn't flash a
// "Disconnected" badge on every rotation cycle. Bounded to MAX_FAST_RECONNECTS
// consecutive attempts so we cannot livelock.
const FAST_RECONNECT_DELAY_MS = 1_000
const FAST_RECONNECT_FRESH_WINDOW_MS = 30_000
const MAX_FAST_RECONNECTS = 5

/**
 * useSSE — hardened SSE consumer.
 *
 * Backend `/events/stream` accepts auth from two sources:
 *   1. acp_token httpOnly cookie  (set by the gateway on /auth/token)
 *   2. Authorization: Bearer …    (impossible from EventSource API, used by SDK)
 *
 * The browser EventSource cannot set custom headers, so we rely on the cookie
 * via `withCredentials: true`. Same-origin requests automatically include it.
 *
 * Features:
 *   - exponential backoff to MAX_BACKOFF_MS
 *   - per-channel `addEventListener` demux via `channels` option
 *   - exposes connection state (connecting | open | closed)
 *   - heartbeat-aware (silent ping every 15s from backend keeps the stream alive)
 *   - heartbeat-freshness watchdog — force-close if backend goes quiet
 *   - surfaces `lastError` ('auth_expired' | 'network' | 'cors' | 'unknown')
 *   - reduced-motion safe (no auto-scroll triggered from here)
 */

export function useSSE({
  enabled = true,
  onMessage,
  onConnected,
  onError,
  channels = {},
  agentId,
} = {}) {
  const esRef              = useRef(null)
  const reconnectTimerRef  = useRef(null)
  const watchdogTimerRef   = useRef(null)
  const lastHeartbeatAtRef = useRef(Date.now())
  const attemptsRef        = useRef(0)
  const mountedRef         = useRef(true)
  const onMessageRef       = useRef(onMessage)
  const onConnectedRef     = useRef(onConnected)
  const onErrorRef         = useRef(onError)
  const channelsRef        = useRef(channels)
  const agentIdRef         = useRef(agentId)
  // Sprint 3: track the most recent error reason and how many consecutive
  // fast-reconnects we've done so we can cap the fast path.
  const lastErrorRef       = useRef(null)
  const fastReconnectCountRef = useRef(0)

  const [state, setState] = useState('connecting')
  // Sprint 2: surface the most recent failure reason so a UI badge can
  // render something more useful than "Disconnected".
  const [lastError, setLastError] = useState(null)

  useEffect(() => { onMessageRef.current   = onMessage   }, [onMessage])
  useEffect(() => { onConnectedRef.current = onConnected }, [onConnected])
  useEffect(() => { onErrorRef.current     = onError     }, [onError])
  useEffect(() => { channelsRef.current    = channels    }, [channels])
  useEffect(() => { agentIdRef.current     = agentId     }, [agentId])

  const buildUrl = () => {
    // Auth flows over the same-origin httpOnly acp_token cookie via
    // withCredentials. Query-string tokens are no longer accepted by the
    // gateway (sprint-1 hardening) so we don't append one.
    const params = new URLSearchParams()
    if (agentIdRef.current) params.set('agent_id', String(agentIdRef.current))
    const qs = params.toString()
    return `${API_BASE}/events/stream${qs ? `?${qs}` : ''}`
  }

  const clearWatchdog = () => {
    if (watchdogTimerRef.current) {
      clearInterval(watchdogTimerRef.current)
      watchdogTimerRef.current = null
    }
  }

  // Forward declaration so the watchdog can call connect() once it expires.
  const connectRef = useRef(null)

  const startWatchdog = () => {
    clearWatchdog()
    lastHeartbeatAtRef.current = Date.now()
    watchdogTimerRef.current = setInterval(() => {
      if (!mountedRef.current) return
      const elapsed = Date.now() - lastHeartbeatAtRef.current
      if (elapsed > HEARTBEAT_TIMEOUT_MS) {
        // No heartbeat for > 45s — the TCP socket may be half-open. Force
        // a fresh EventSource so the browser actually re-resolves DNS and
        // (more importantly) re-reads our possibly-refreshed query token.
        setLastError('heartbeat_timeout')
        try { esRef.current?.close() } catch { /* ignore */ }
        esRef.current = null
        clearWatchdog()
        if (mountedRef.current) {
          attemptsRef.current = 0
          connectRef.current?.()
        }
      }
    }, HEARTBEAT_WATCHDOG_INTERVAL_MS)
  }

  const connect = useCallback(() => {
    if (!mountedRef.current || !enabled) return
    setState('connecting')

    const backoffMs = Math.min(1_000 * 2 ** attemptsRef.current, MAX_BACKOFF_MS)
    const es = new EventSource(buildUrl(), { withCredentials: true })
    esRef.current = es

    es.addEventListener('connected', (e) => {
      attemptsRef.current = 0
      // Successful (re)connection — clear both error trail and the fast-
      // reconnect budget so the next disconnect starts fresh.
      fastReconnectCountRef.current = 0
      lastErrorRef.current = null
      setState('open')
      setLastError(null)
      lastHeartbeatAtRef.current = Date.now()
      startWatchdog()
      try {
        onConnectedRef.current?.(JSON.parse(e.data))
      } catch {
        onConnectedRef.current?.(e.data)
      }
    })

    es.addEventListener('heartbeat', () => {
      // Keep-alive — refresh the watchdog timestamp so we don't kill a
      // healthy connection on the next interval tick.
      lastHeartbeatAtRef.current = Date.now()
    })

    // Channel demux: subscribers can listen to named server events.
    for (const [name, fn] of Object.entries(channelsRef.current || {})) {
      es.addEventListener(name, (e) => {
        lastHeartbeatAtRef.current = Date.now()
        try {
          fn(JSON.parse(e.data))
        } catch {
          fn(e.data)
        }
      })
    }

    es.onmessage = (event) => {
      lastHeartbeatAtRef.current = Date.now()
      try {
        onMessageRef.current?.(JSON.parse(event.data))
      } catch {
        // malformed SSE frame — ignore
      }
    }

    es.onerror = () => {
      // Classify the failure reason for the UI badge. EventSource onerror
      // doesn't expose the underlying HTTP status, so we use heuristics:
      // - never reached 'open' AND session is still valid (cookie/token
      //   present, expiry in the future) → likely network or transient
      //   server unavailability, NOT auth.
      // - never reached 'open' AND no session signal → genuine auth_expired.
      // - was open before erroring → network drop.
      const wasOpen = es.readyState === EventSource.OPEN
      const expiry = parseInt(localStorage.getItem("acp_token_expiry") || "0", 10)
      const sessionLooksValid = expiry > Date.now()
      // Snapshot the previous error reason BEFORE we overwrite it — the
      // fast-reconnect branch needs to know that we weren't already in an
      // auth_expired state (which routes to IncidentOverlay).
      const prevError = lastErrorRef.current
      let nextError
      if (wasOpen) {
        nextError = 'network'
      } else if (sessionLooksValid) {
        nextError = 'network'
      } else if (getCurrentToken() || localStorage.getItem("tenant_id")) {
        // Session metadata present but expiry past → really expired.
        nextError = 'auth_expired'
      } else {
        nextError = 'network'
      }
      lastErrorRef.current = nextError
      setLastError(nextError)

      es.close()
      esRef.current = null
      clearWatchdog()
      attemptsRef.current += 1
      setState('closed')
      onErrorRef.current?.()

      // Sprint 3 fast-reconnect: when the session is clearly still fresh
      // (cookie expiry > 30s away) AND we never tripped auth_expired (this
      // disconnect OR the previous one), this is almost certainly a Clerk
      // token-rotation race rather than a real outage. Reconnect quickly
      // and skip the exponential backoff. Capped at MAX_FAST_RECONNECTS so a
      // pathological server can't trap us in a 1s reconnect loop forever.
      const freshSession =
        expiry > 0 && (expiry - Date.now()) > FAST_RECONNECT_FRESH_WINDOW_MS
      const errorIsBenign = nextError !== 'auth_expired'
      const prevWasBenign = prevError === null || prevError === 'network'
      const fastBudgetLeft =
        fastReconnectCountRef.current < MAX_FAST_RECONNECTS
      const useFastReconnect =
        freshSession && errorIsBenign && prevWasBenign && fastBudgetLeft

      const delay = useFastReconnect ? FAST_RECONNECT_DELAY_MS : backoffMs
      if (useFastReconnect) {
        fastReconnectCountRef.current += 1
        // Don't grow exponential backoff while on the fast path — successful
        // reconnect will reset attemptsRef via the 'connected' handler. If
        // we end up falling back to exponential, we still want to start
        // from a sane attempt count.
        attemptsRef.current = Math.max(0, attemptsRef.current - 1)
      } else {
        // Exiting the fast path → reset the budget so a future fresh-
        // session disconnect gets its full 5 attempts again.
        fastReconnectCountRef.current = 0
      }

      if (mountedRef.current) {
        reconnectTimerRef.current = setTimeout(connect, delay)
      }
    }
  }, [enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { connectRef.current = connect }, [connect])

  useEffect(() => {
    mountedRef.current = true
    if (enabled) connect()
    return () => {
      mountedRef.current = false
      clearTimeout(reconnectTimerRef.current)
      clearWatchdog()
      esRef.current?.close()
      esRef.current = null
    }
  }, [enabled, connect])

  const reconnect = useCallback(() => {
    clearTimeout(reconnectTimerRef.current)
    clearWatchdog()
    esRef.current?.close()
    esRef.current = null
    attemptsRef.current = 0
    fastReconnectCountRef.current = 0
    lastErrorRef.current = null
    setLastError(null)
    connect()
  }, [connect])

  return { reconnect, state, lastError }
}
