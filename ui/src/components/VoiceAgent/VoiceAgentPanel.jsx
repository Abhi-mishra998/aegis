import React, { useEffect, useRef, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { X, Mic, MicOff, AlertCircle } from 'lucide-react'
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  useLocalParticipant,
  useRoomContext,
} from '@livekit/components-react'
import { Track } from 'livekit-client'

import AnimatedOrb, { STATE_COLORS } from './AnimatedOrb'
import { voiceService, parseApiError } from '../../services/api'

/**
 * Killer voice-agent panel. Full-viewport dim overlay, centered animated
 * orb that breathes with the conversation, live transcript on the right.
 *
 * Lifecycle:
 *   1. Open  →  POST nothing; GET /voice/token to mint a LiveKit JWT.
 *   2. Connect  →  LiveKitRoom uses the token + url, the room opens.
 *   3. LiveKit Cloud sees RoomAgentDispatch and dispatches the EC2 worker.
 *   4. Worker joins, speaks the greeting.
 *   5. User talks  →  STT  →  RAG  →  LLM  →  TTS  →  agent voice.
 *   6. Close  →  disconnects, room expires server-side after TTL.
 */
export default function VoiceAgentPanel({ open, onClose }) {
  const [creds, setCreds]   = useState(null)   // {token, url, room, session_max_seconds}
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  // Fetch credentials lazily — only on first open. Uses voiceService so the
  // Clerk Bearer header is attached the same way every other API call does;
  // a raw fetch() here previously left the gateway unable to identify the
  // user when only the Clerk session (not the legacy cookie) was active.
  const fetchCreds = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await voiceService.getToken()
      if (!data?.token || !data?.url) {
        throw new Error('gateway returned no token')
      }
      setCreds({
        token: data.token,
        url: data.url,
        room: data.room,
        // Gateway echoes the agent's AEGIS_SESSION_MAX_SECONDS so the UI
        // countdown matches the server-side hard cap. Fall back to 1800
        // (the agent default) if the gateway response omits the field.
        sessionMaxSeconds: data.session_max_seconds || 1800,
      })
    } catch (e) {
      if (e?._status === 503) {
        setError('voice agent not configured on the gateway')
      } else {
        setError(parseApiError(e, 'voice agent is unavailable'))
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open && !creds && !loading && !error) {
      fetchCreds()
    }
  }, [open, creds, loading, error, fetchCreds])

  // Lock body scroll while open
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [open])

  // Escape to close
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') handleClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const handleClose = () => {
    setCreds(null)
    setError('')
    onClose?.()
  }

  if (!open) return null

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Aegis voice agent"
      className="fixed inset-0 z-[70] flex animate-fade-in"
      style={{
        background: 'radial-gradient(ellipse at center, rgba(20,20,30,0.96) 0%, rgba(3,3,5,0.99) 70%)',
        backdropFilter: 'blur(8px)',
      }}
    >
      {/* Close button — top-right of the entire overlay */}
      <button
        onClick={handleClose}
        aria-label="Close voice agent"
        className="absolute top-5 right-5 p-2.5 rounded-full bg-white/[0.06] hover:bg-white/[0.12] border border-white/[0.08] hover:border-white/[0.18] transition-all z-10"
      >
        <X size={18} className="text-white" aria-hidden="true" />
      </button>

      {error && !creds && (
        <ErrorState message={error} onRetry={fetchCreds} onClose={handleClose} />
      )}

      {loading && !error && !creds && (
        <LoadingState />
      )}

      {creds && (
        <LiveKitRoom
          token={creds.token}
          serverUrl={creds.url}
          connect
          audio
          video={false}
          onDisconnected={handleClose}
          onError={(e) => setError(e?.message || 'livekit connection error')}
        >
          <RoomAudioRenderer />
          <VoiceSession onClose={handleClose} sessionMaxSeconds={creds.sessionMaxSeconds} />
        </LiveKitRoom>
      )}
    </div>,
    document.body,
  )
}

/* ── Inner session view (rendered inside LiveKitRoom context) ────────── */

function VoiceSession({ onClose, sessionMaxSeconds = 1800 }) {
  const voice = useVoiceAssistant() // { state, agent, agentTranscriptions, ... }
  const { localParticipant } = useLocalParticipant()
  const room = useRoomContext()
  const [muted, setMuted]               = useState(false)
  const [audioLevel, setAudioLevel]     = useState(0)
  const [transcript, setTranscript]     = useState([])
  const [latencyMs, setLatencyMs]       = useState(null)
  const [secondsLeft, setSecondsLeft]   = useState(sessionMaxSeconds)

  // Session countdown — matches the gateway JWT TTL and the agent's
  // SESSION_MAX_SECONDS hard cap. When it hits zero we close the panel.
  useEffect(() => {
    setSecondsLeft(sessionMaxSeconds)
    const t = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(t)
          // Tiny delay so the timer reads 0 before the panel unmounts.
          setTimeout(() => onClose?.(), 400)
          return 0
        }
        return s - 1
      })
    }, 1000)
    return () => clearInterval(t)
  }, [sessionMaxSeconds, onClose])

  // Pipe mic + agent volumes into a single 0..1 value driving the orb.
  // When the user is speaking → use mic level. When the agent is speaking →
  // use the agent audio track level. Idle → 0 (orb breathes on its own).
  useEffect(() => {
    if (!room) return
    let raf = 0
    let micAnalyser = null
    let agentAnalyser = null
    let audioCtx = null

    const setupMic = async () => {
      try {
        const micTrack = localParticipant?.getTrackPublication?.(Track.Source.Microphone)?.track
        const micMs = micTrack?.mediaStreamTrack
        if (!micMs) return
        audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)()
        const stream = new MediaStream([micMs])
        const src = audioCtx.createMediaStreamSource(stream)
        micAnalyser = audioCtx.createAnalyser()
        micAnalyser.fftSize = 256
        src.connect(micAnalyser)
      } catch { /* no-op — mic not available yet */ }
    }
    setupMic()

    const setupAgentAudio = () => {
      try {
        const agentParticipant = voice?.agent
        if (!agentParticipant) return
        const pubs = Array.from(agentParticipant.audioTrackPublications?.values() || [])
        const track = pubs[0]?.track
        const ms = track?.mediaStreamTrack
        if (!ms) return
        audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)()
        const stream = new MediaStream([ms])
        const src = audioCtx.createMediaStreamSource(stream)
        agentAnalyser = audioCtx.createAnalyser()
        agentAnalyser.fftSize = 256
        src.connect(agentAnalyser)
      } catch { /* */ }
    }
    setupAgentAudio()

    const buf = new Uint8Array(128)
    const sample = (analyser) => {
      if (!analyser) return 0
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128
        sum += v * v
      }
      return Math.min(1, Math.sqrt(sum / buf.length) * 3.5)
    }

    const loop = () => {
      const u = sample(micAnalyser)
      const a = sample(agentAnalyser)
      setAudioLevel(Math.max(u, a))
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      audioCtx?.close()
    }
  }, [room, localParticipant, voice?.agent])

  // Append final transcripts (user + agent) into one ordered list.
  useEffect(() => {
    if (!voice) return
    const all = []
    for (const seg of (voice.agentTranscriptions || [])) {
      all.push({ id: `a-${seg.id}`, role: 'agent', text: seg.text, final: seg.final })
    }
    // user transcripts come through room.localParticipant transcription
    // events, also surfaced on voice for convenience
    for (const seg of (voice.userTranscriptions || [])) {
      all.push({ id: `u-${seg.id}`, role: 'user', text: seg.text, final: seg.final })
    }
    // Sort by id chronologically (segment IDs are monotonic per turn)
    all.sort((x, y) => x.id.localeCompare(y.id))
    setTranscript(all)
  }, [voice?.agentTranscriptions, voice?.userTranscriptions, voice])

  // Listen for room rtc stats once per second for the latency badge
  useEffect(() => {
    if (!room) return
    const t = setInterval(async () => {
      try {
        const stats = await room.engine?.publisher?.getStats?.()
        if (!stats) return
        let rtt = null
        stats.forEach((s) => {
          if (s.type === 'candidate-pair' && s.state === 'succeeded' && s.currentRoundTripTime != null) {
            rtt = s.currentRoundTripTime * 1000
          }
        })
        if (rtt != null) setLatencyMs(Math.round(rtt))
      } catch { /* */ }
    }, 1000)
    return () => clearInterval(t)
  }, [room])

  const toggleMute = async () => {
    if (!localParticipant) return
    await localParticipant.setMicrophoneEnabled(muted)
    setMuted(!muted)
  }

  const stateLabel = STATE_COLORS[voice.state]?.name || voice.state || 'Idle'

  return (
    <div className="flex flex-1 h-full">
      {/* ── Left: animated orb + state + controls ── */}
      <div className="flex-1 flex flex-col items-center justify-center px-8">
        <AnimatedOrb state={voice.state || 'connecting'} audioLevel={audioLevel} size={300} />

        <div className="mt-10 text-center">
          <p className="label-standard text-neutral-500">Aegis Voice Guide</p>
          <h2
            className="text-3xl font-bold tracking-tight uppercase mt-2"
            style={{
              color: `hsl(${STATE_COLORS[voice.state]?.hue ?? 200}, ${STATE_COLORS[voice.state]?.sat ?? 30}%, 75%)`,
              textShadow: `0 0 24px hsla(${STATE_COLORS[voice.state]?.hue ?? 200}, 80%, 60%, 0.35)`,
            }}
          >
            {stateLabel}
          </h2>
          {voice.state === 'connecting' && (
            <p className="text-xs text-neutral-500 mt-2">Dispatching aegis-guide worker…</p>
          )}
        </div>

        {/* Bottom controls */}
        <div className="mt-12 flex items-center gap-3">
          <button
            onClick={toggleMute}
            aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
            className={`p-3.5 rounded-full border transition-all ${
              muted
                ? 'bg-red-500/15 border-red-500/30 text-red-300 hover:bg-red-500/25'
                : 'bg-white/[0.06] border-white/[0.12] text-white hover:bg-white/[0.10]'
            }`}
          >
            {muted ? <MicOff size={18} /> : <Mic size={18} />}
          </button>

          <button
            onClick={onClose}
            className="px-5 h-12 rounded-full bg-red-500/85 hover:bg-red-500 text-white text-sm font-semibold transition-colors flex items-center gap-2"
          >
            <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
            End conversation
          </button>

          {latencyMs != null && (
            <div className="ml-3 px-2.5 py-1 rounded-full bg-white/[0.04] border border-white/[0.06] text-[10px] text-neutral-400 font-mono">
              rtt {latencyMs}ms
            </div>
          )}

          {/* Session countdown — caps quota burn per session */}
          <div
            className={`px-2.5 py-1 rounded-full border text-[10px] font-mono ${
              secondsLeft <= 30
                ? 'bg-red-500/15 border-red-500/30 text-red-300'
                : secondsLeft <= 60
                  ? 'bg-amber-500/15 border-amber-500/30 text-amber-300'
                  : 'bg-white/[0.04] border-white/[0.06] text-neutral-400'
            }`}
            title="Session auto-closes to cap LLM/STT/TTS quota usage"
          >
            {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, '0')}
          </div>
        </div>
      </div>

      {/* ── Right: live transcript ── */}
      <aside className="hidden md:flex w-[380px] shrink-0 border-l border-white/[0.06] flex-col bg-black/40">
        <div className="px-5 py-4 border-b border-white/[0.06] flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-white">Live Transcript</p>
            <p className="text-[10px] text-neutral-500 mt-0.5">Deepgram nova-3 · Cartesia sonic-3</p>
          </div>
          <span className="px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30 text-[9px] font-bold uppercase text-green-300">
            Recording
          </span>
        </div>

        <TranscriptStream items={transcript} />

        <div className="px-5 py-3 border-t border-white/[0.06]">
          <p className="text-[10px] text-neutral-600 leading-snug">
            Grounded by hybrid BM25 + dense + cross-encoder retrieval over the Aegis docs.
            Try: "what is the kill switch?", "explain the audit chain", "how do demo packs work?"
          </p>
        </div>
      </aside>
    </div>
  )
}

/* ── Transcript stream ──────────────────────────────────────────────── */

function TranscriptStream({ items }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items])

  if (!items.length) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
        <div className="w-12 h-12 rounded-full border border-white/[0.08] flex items-center justify-center mb-3">
          <Mic size={18} className="text-neutral-500" />
        </div>
        <p className="text-xs text-neutral-500">
          Start talking — your words and the agent's reply will stream here.
        </p>
      </div>
    )
  }

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
      {items.map((seg) => (
        <div
          key={seg.id}
          className={`flex ${seg.role === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          <div
            className={`max-w-[88%] px-3 py-2 rounded-xl text-xs leading-relaxed ${
              seg.role === 'user'
                ? 'bg-white/[0.08] border border-white/[0.10] text-white rounded-br-sm'
                : 'bg-blue-500/[0.10] border border-blue-500/20 text-blue-100 rounded-bl-sm'
            } ${seg.final ? '' : 'opacity-70'}`}
          >
            <p className="text-[10px] uppercase tracking-wider opacity-60 mb-1">
              {seg.role === 'user' ? 'You' : 'Aegis Guide'}
            </p>
            {seg.text}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── States: loading + error ────────────────────────────────────────── */

function LoadingState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center">
      <AnimatedOrb state="connecting" audioLevel={0} size={260} />
      <p className="mt-8 text-sm text-neutral-300">Minting LiveKit token…</p>
      <p className="text-[11px] text-neutral-500 mt-1">
        Connecting your browser to the Aegis Voice Guide worker.
      </p>
    </div>
  )
}

function ErrorState({ message, onRetry, onClose }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 text-center max-w-md mx-auto">
      <div className="w-14 h-14 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center mb-4">
        <AlertCircle size={24} className="text-red-400" aria-hidden="true" />
      </div>
      <h2 className="text-xl font-bold text-white mb-2">Voice Guide unavailable</h2>
      <p className="text-sm text-neutral-400 mb-1">{message}</p>
      <p className="text-[11px] text-neutral-600 mb-6">
        The Voice Guide worker runs on a sibling EC2. If it's stopped to save cost,
        an operator needs to start it before this works.
      </p>
      <div className="flex gap-2">
        <button
          onClick={onRetry}
          className="px-4 h-10 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] border border-white/[0.08] text-sm text-white transition-colors"
        >
          Retry
        </button>
        <button
          onClick={onClose}
          className="px-4 h-10 rounded-lg bg-red-500/15 hover:bg-red-500/25 border border-red-500/30 text-sm text-red-300 transition-colors"
        >
          Close
        </button>
      </div>
    </div>
  )
}
