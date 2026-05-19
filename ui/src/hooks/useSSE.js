import { useEffect, useRef, useCallback, useState } from 'react'

const API_BASE = import.meta.env.VITE_GATEWAY_URL || ''
const MAX_BACKOFF_MS = 32_000

/**
 * useSSE — hardened SSE consumer.
 *
 * Backend `/events/stream` accepts auth from three sources:
 *   1. acp_token httpOnly cookie  (production dashboard)
 *   2. Authorization: Bearer …    (impossible from EventSource API)
 *   3. ?token=…                   (cookieless / cross-origin SDK case)
 *
 * The browser EventSource cannot set custom headers, so we rely on the cookie
 * via `withCredentials: true` AND optionally append a query-string token when
 * `localStorage.sse_query_token` is present (CSRF-safe — same-origin only).
 *
 * Features:
 *   - exponential backoff to MAX_BACKOFF_MS
 *   - per-channel `addEventListener` demux via `channels` option
 *   - exposes connection state (connecting | open | closed)
 *   - heartbeat-aware (silent ping every 15s from backend keeps the stream alive)
 *   - reduced-motion safe (no auto-scroll triggered from here)
 */
export function useSSE({
  enabled = true,
  onMessage,
  onConnected,
  onError,
  channels = {},
} = {}) {
  const esRef              = useRef(null)
  const reconnectTimerRef  = useRef(null)
  const attemptsRef        = useRef(0)
  const mountedRef         = useRef(true)
  const onMessageRef       = useRef(onMessage)
  const onConnectedRef     = useRef(onConnected)
  const onErrorRef         = useRef(onError)
  const channelsRef        = useRef(channels)

  const [state, setState] = useState('connecting')

  useEffect(() => { onMessageRef.current   = onMessage   }, [onMessage])
  useEffect(() => { onConnectedRef.current = onConnected }, [onConnected])
  useEffect(() => { onErrorRef.current     = onError     }, [onError])
  useEffect(() => { channelsRef.current    = channels    }, [channels])

  const buildUrl = () => {
    // Optional query-token override for environments where cookies are not
    // shared with the API origin (cross-origin SaaS, mobile webviews).
    let qs = ''
    try {
      const t = localStorage.getItem('sse_query_token')
      if (t) qs = `?token=${encodeURIComponent(t)}`
    } catch { /* localStorage unavailable */ }
    return `${API_BASE}/events/stream${qs}`
  }

  const connect = useCallback(() => {
    if (!mountedRef.current || !enabled) return
    setState('connecting')

    const backoffMs = Math.min(1_000 * 2 ** attemptsRef.current, MAX_BACKOFF_MS)
    const es = new EventSource(buildUrl(), { withCredentials: true })
    esRef.current = es

    es.addEventListener('connected', (e) => {
      attemptsRef.current = 0
      setState('open')
      onConnectedRef.current?.(e.data)
    })

    es.addEventListener('heartbeat', () => {
      // Keep-alive only — no payload. Presence is enough.
    })

    // Channel demux: subscribers can listen to named server events.
    for (const [name, fn] of Object.entries(channelsRef.current || {})) {
      es.addEventListener(name, (e) => {
        try {
          fn(JSON.parse(e.data))
        } catch {
          fn(e.data)
        }
      })
    }

    es.onmessage = (event) => {
      try {
        onMessageRef.current?.(JSON.parse(event.data))
      } catch {
        // malformed SSE frame — ignore
      }
    }

    es.onerror = () => {
      es.close()
      esRef.current = null
      attemptsRef.current += 1
      setState('closed')
      onErrorRef.current?.()
      if (mountedRef.current) {
        reconnectTimerRef.current = setTimeout(connect, backoffMs)
      }
    }
  }, [enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    mountedRef.current = true
    if (enabled) connect()
    return () => {
      mountedRef.current = false
      clearTimeout(reconnectTimerRef.current)
      esRef.current?.close()
      esRef.current = null
    }
  }, [enabled, connect])

  const reconnect = useCallback(() => {
    clearTimeout(reconnectTimerRef.current)
    esRef.current?.close()
    esRef.current = null
    attemptsRef.current = 0
    connect()
  }, [connect])

  return { reconnect, state }
}
