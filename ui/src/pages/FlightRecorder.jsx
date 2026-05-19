import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { RefreshCw, Play, Pause, SkipForward, SkipBack, Search, Film, ShieldCheck, Download, Anchor } from 'lucide-react'
import { flightService, receiptService, transparencyService } from '../services/api'

const STEP_COLOR = {
  prompt:    '#a78bfa',
  tool_call: '#34d399',
  policy:    '#60a5fa',
  decision:  '#fbbf24',
  retry:     '#f97316',
  failure:   '#ef4444',
}

function fmtMs(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export default function FlightRecorder() {
  const [timelines, setTimelines] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ minutes: 5, status: '', tool: '' })
  const [selected, setSelected] = useState(null)
  const [replay, setReplay] = useState(null)
  const [stepIdx, setStepIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState(null)
  const [receiptError, setReceiptError] = useState('')
  const [inclusion, setInclusion] = useState(null)
  const [inclusionError, setInclusionError] = useState('')

  const fetchTimelines = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await flightService.listTimelines(filter)
      setTimelines(res?.data || [])
    } catch (e) {
      // 2026-05-14: surface fetch failures so the operator sees them; previous
      // console.warn made flight-recorder look frozen on a backend outage.
      setError(e?.message || 'Flight recorder unreachable')
    }
    finally { setLoading(false) }
  }, [filter])

  useEffect(() => {
    fetchTimelines()
    const t = setInterval(fetchTimelines, 30_000)
    return () => clearInterval(t)
  }, [fetchTimelines])

  const loadReplay = async (t) => {
    setSelected(t); setStepIdx(0); setPlaying(false)
    setReceipt(null); setReceiptError('')
    setInclusion(null); setInclusionError('')
    const res = await flightService.getReplay(t.id)
    setReplay(res?.data || null)

    // Fire-and-forget: fetch signed receipt + inclusion proof in parallel.
    // 404s are expected for fresh timelines whose audit row hasn't landed yet.
    const execId = res?.data?.timeline?.request_id || t.request_id || t.id
    if (execId) {
      receiptService.getReceipt(execId)
        .then((r) => setReceipt(r?.data || null))
        .catch((e) => setReceiptError(e?.message || 'receipt unavailable'))
      transparencyService.getInclusion(execId)
        .then((r) => setInclusion(r?.data || null))
        .catch((e) => setInclusionError(e?.message || 'inclusion proof unavailable'))
    }
  }

  const downloadReceipt = () => {
    if (!receipt) return
    const blob = new Blob([JSON.stringify(receipt, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `acp-receipt-${receipt.receipt?.execution_id || 'unknown'}.json`
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  }

  const downloadInclusion = () => {
    if (!inclusion) return
    const blob = new Blob([JSON.stringify(inclusion, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `acp-inclusion-${inclusion.root_date || 'unknown'}.json`
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  }

  // Playback
  useEffect(() => {
    if (!playing || !replay) return
    const tk = setInterval(() => {
      setStepIdx((i) => {
        const n = (replay.steps || []).length
        if (i + 1 >= n) { setPlaying(false); return n - 1 }
        return i + 1
      })
    }, 500)
    return () => clearInterval(tk)
  }, [playing, replay])

  const currentStep = useMemo(
    () => replay?.steps?.[stepIdx] || null,
    [replay, stepIdx],
  )

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2"><Film size={20} /> Flight Recorder</h1>
          <p className="text-xs text-neutral-500 mt-1">Replayable runtime execution timelines · step-by-step playback</p>
        </div>
        <button onClick={fetchTimelines} disabled={loading}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-xs text-neutral-300 hover:bg-white/10 disabled:opacity-50">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-400 flex items-center justify-between" role="alert">
          <span>Flight recorder: {error}</span>
          <button onClick={fetchTimelines} className="text-red-300 underline">Retry</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
          <div className="flex items-center gap-2 mb-3">
            <Search size={13} className="text-neutral-400" />
            <span className="text-sm font-semibold text-white">Timelines</span>
            <span className="ml-auto text-[10px] font-mono text-neutral-600">{timelines.length}</span>
          </div>
          <div className="flex gap-1 mb-3 text-[10px] flex-wrap">
            {[2, 5, 15, 60, 360, 1440, 4320].map((m) => (
              <button key={m} onClick={() => setFilter((f) => ({ ...f, minutes: m }))}
                className={`px-2 py-1 rounded ${filter.minutes === m ? 'bg-white/10 text-white' : 'text-neutral-500 hover:text-white'}`}>
                {m < 60 ? `${m}m` : m < 1440 ? `${m / 60}h` : `${m / 1440}d`}
              </button>
            ))}
          </div>
          <div className="max-h-[500px] overflow-y-auto divide-y divide-white/5">
            {timelines.map((t) => (
              <button key={t.id} onClick={() => loadReplay(t)}
                className={`w-full text-left px-3 py-2 hover:bg-white/[0.04] transition-colors ${selected?.id === t.id ? 'bg-white/[0.06]' : ''}`}>
                <div className="flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    t.status === 'ok' ? 'bg-green-500' : t.status === 'error' ? 'bg-red-500' : 'bg-amber-400'
                  }`} />
                  <span className="text-xs font-mono text-white truncate flex-1">{t.tool || '—'}</span>
                  <span className="text-[10px] font-mono text-neutral-600">{fmtMs(t.duration_ms)}</span>
                </div>
                <div className="text-[10px] font-mono text-neutral-600 mt-0.5 truncate">
                  {t.request_id?.slice(0, 16)} · {t.final_decision || '—'}
                </div>
              </button>
            ))}
            {!timelines.length && !loading && (
              <p className="text-xs text-neutral-600 text-center p-6">No timelines in the window.</p>
            )}
          </div>
        </div>

        <div className="lg:col-span-2 rounded-2xl border border-white/10 bg-[#0a0a0a] p-4">
          {!replay ? (
            <p className="text-xs text-neutral-600 text-center p-12">Select a timeline to replay.</p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-sm font-semibold text-white">Replay</span>
                <span className="text-[10px] font-mono text-neutral-500">{replay.steps.length} steps</span>
                <div className="ml-auto flex items-center gap-1">
                  <button onClick={() => setStepIdx((i) => Math.max(0, i - 1))} className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10"><SkipBack size={13} className="text-white" /></button>
                  <button onClick={() => setPlaying((p) => !p)} className="p-1.5 rounded-lg bg-blue-500/20 hover:bg-blue-500/30">
                    {playing ? <Pause size={13} className="text-blue-300" /> : <Play size={13} className="text-blue-300" />}
                  </button>
                  <button onClick={() => setStepIdx((i) => Math.min(replay.steps.length - 1, i + 1))} className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10"><SkipForward size={13} className="text-white" /></button>
                </div>
              </div>

              <input
                type="range" min={0} max={Math.max(0, replay.steps.length - 1)} value={stepIdx}
                onChange={(e) => setStepIdx(Number(e.target.value))}
                className="w-full"
              />

              <div className="mt-3 grid grid-cols-12 gap-3">
                <div className="col-span-12 lg:col-span-5 space-y-1 max-h-[360px] overflow-y-auto">
                  {replay.steps.map((s, i) => (
                    <button key={s.id} onClick={() => setStepIdx(i)}
                      className={`w-full text-left rounded-lg px-2 py-1.5 transition-colors ${i === stepIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'}`}>
                      <div className="flex items-center gap-2">
                        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: STEP_COLOR[s.step_type] || '#525252' }} />
                        <span className="text-[10px] font-mono text-neutral-300 w-6">{i}</span>
                        <span className="text-[10px] font-mono text-white truncate flex-1">{s.step_type}</span>
                        <span className="text-[10px] font-mono text-neutral-500">{fmtMs(s.latency_ms)}</span>
                      </div>
                      {s.summary && <p className="text-[10px] text-neutral-500 truncate mt-0.5 pl-3.5">{s.summary}</p>}
                    </button>
                  ))}
                </div>
                <div className="col-span-12 lg:col-span-7 rounded-xl bg-black/40 border border-white/5 p-3 font-mono text-[11px] text-neutral-300 overflow-auto max-h-[360px]">
                  {currentStep ? (
                    <>
                      <p className="text-[10px] text-neutral-500 mb-2">step {stepIdx} · {currentStep.step_type} · status {currentStep.status}</p>
                      <pre className="whitespace-pre-wrap">{JSON.stringify(currentStep.payload, null, 2)}</pre>
                    </>
                  ) : <p className="text-neutral-600">no step selected</p>}
                </div>
              </div>

              <div className="mt-3 pt-3 border-t border-white/5 grid grid-cols-4 gap-3 text-[10px]">
                <div><span className="text-neutral-500">final decision</span><div className="text-white font-mono">{replay.timeline.final_decision || '—'}</div></div>
                <div><span className="text-neutral-500">final risk</span><div className="text-white font-mono">{replay.timeline.final_risk?.toFixed?.(3) || '—'}</div></div>
                <div><span className="text-neutral-500">duration</span><div className="text-white font-mono">{fmtMs(replay.timeline.duration_ms)}</div></div>
                <div><span className="text-neutral-500">snapshots</span><div className="text-white font-mono">{replay.snapshots?.length || 0}</div></div>
              </div>

              {/* Cryptographic receipt badge — sigstore-for-agents */}
              <div className="mt-3 pt-3 border-t border-white/5">
                {receipt ? (
                  <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
                    <ShieldCheck size={14} className="text-emerald-400 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="text-[11px] text-emerald-300 font-medium">Signed by ACP · ed25519</div>
                      <div className="text-[10px] font-mono text-neutral-500 truncate">
                        fp {receipt.public_key_fingerprint} · exec {receipt.receipt?.execution_id?.slice(0, 8)}…
                      </div>
                    </div>
                    <button
                      onClick={downloadReceipt}
                      title="Download receipt for offline verification"
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 text-[10px] text-neutral-300"
                    >
                      <Download size={11} /> JSON
                    </button>
                  </div>
                ) : receiptError ? (
                  <div className="px-3 py-2 rounded-lg bg-white/[0.02] border border-white/5 text-[10px] text-neutral-500">
                    receipt: {receiptError}
                  </div>
                ) : (
                  <div className="px-3 py-2 rounded-lg bg-white/[0.02] border border-white/5 text-[10px] text-neutral-600">
                    fetching cryptographic receipt…
                  </div>
                )}

                {/* Transparency-log inclusion proof */}
                {inclusion && (
                  <div className={`mt-2 flex items-center gap-3 px-3 py-2 rounded-lg border ${
                    inclusion.pending
                      ? 'bg-amber-500/5 border-amber-500/20'
                      : 'bg-sky-500/5 border-sky-500/20'
                  }`}>
                    <Anchor size={14} className={inclusion.pending ? 'text-amber-400 shrink-0' : 'text-sky-400 shrink-0'} />
                    <div className="flex-1 min-w-0">
                      <div className={`text-[11px] font-medium ${inclusion.pending ? 'text-amber-300' : 'text-sky-300'}`}>
                        {inclusion.pending
                          ? `Pending end-of-day commitment · ${inclusion.root_date}`
                          : `Anchored in ${inclusion.root_date} Merkle root`}
                      </div>
                      <div className="text-[10px] font-mono text-neutral-500 truncate">
                        root {(inclusion.proof?.root || '').slice(0, 16)}… · index {inclusion.proof?.index} of {inclusion.proof?.size}
                      </div>
                    </div>
                    <button
                      onClick={downloadInclusion}
                      title="Download inclusion proof for offline verification"
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 text-[10px] text-neutral-300"
                    >
                      <Download size={11} /> JSON
                    </button>
                  </div>
                )}
                {!inclusion && inclusionError && (
                  <div className="mt-2 px-3 py-2 rounded-lg bg-white/[0.02] border border-white/5 text-[10px] text-neutral-500">
                    inclusion proof: {inclusionError}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
