import React, { useMemo, useState } from 'react'
import { Columns, AlignJustify } from 'lucide-react'

/**
 * DiffViewer — text diff renderer used by PolicyBuilder version history and
 * any governance comparison view.
 *
 *   <DiffViewer
 *     before={policyV1}
 *     after={policyV2}
 *     beforeLabel="v3"
 *     afterLabel="v4 (current)"
 *     defaultMode="unified"      // or "split"
 *   />
 *
 * Pure-CSS rendering — no diff library dependency. The diff algorithm is a
 * minimal LCS variant on lines, which is fine for OPA/policy bundles
 * (typically < 1000 lines). For larger inputs we render a "too large to
 * diff" message rather than blocking the UI.
 */
const MAX_DIFF_LINES = 5000

function lcsTable(a, b) {
  const m = a.length
  const n = b.length
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1))
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (a[i] === b[j]) dp[i][j] = dp[i + 1][j + 1] + 1
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  return dp
}

function computeDiff(before, after) {
  const a = (before || '').split('\n')
  const b = (after || '').split('\n')
  if (a.length + b.length > MAX_DIFF_LINES) {
    return { tooLarge: true, lines: [] }
  }
  const dp = lcsTable(a, b)
  const lines = []
  let i = 0, j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ type: 'context', a: a[i], b: b[j], li: i + 1, ri: j + 1 })
      i++; j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push({ type: 'del', a: a[i], b: '', li: i + 1, ri: null })
      i++
    } else {
      lines.push({ type: 'add', a: '', b: b[j], li: null, ri: j + 1 })
      j++
    }
  }
  while (i < a.length) { lines.push({ type: 'del', a: a[i], b: '', li: i + 1, ri: null }); i++ }
  while (j < b.length) { lines.push({ type: 'add', a: '', b: b[j], li: null, ri: j + 1 }); j++ }
  return { tooLarge: false, lines }
}

const ROW_STYLES = {
  context: 'bg-transparent text-neutral-400',
  add:     'bg-emerald-500/[0.08] text-emerald-200',
  del:     'bg-red-500/[0.08]     text-red-200',
}

const SIGIL = { context: ' ', add: '+', del: '−' }

export default function DiffViewer({
  before = '',
  after = '',
  beforeLabel = 'before',
  afterLabel = 'after',
  defaultMode = 'unified',
  className = '',
}) {
  const [mode, setMode] = useState(defaultMode)
  const diff = useMemo(() => computeDiff(before, after), [before, after])
  const stats = useMemo(() => {
    let added = 0, removed = 0
    for (const l of diff.lines) {
      if (l.type === 'add') added++
      else if (l.type === 'del') removed++
    }
    return { added, removed }
  }, [diff])

  if (diff.tooLarge) {
    return (
      <div className={`p-6 text-center text-xs text-neutral-500 border border-dashed border-[var(--border-default)] rounded-2xl ${className}`}>
        Diff too large to render inline ({(before.length + after.length).toLocaleString()} chars).
        Export both versions to your local diff tool.
      </div>
    )
  }

  return (
    <div className={`border border-[var(--border-subtle)] bg-[var(--bg-surface)] rounded-2xl overflow-hidden ${className}`}>
      <div className="px-4 py-2 border-b border-[var(--border-subtle)] flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-[10px] font-bold uppercase tracking-wider text-neutral-500 truncate">
            {beforeLabel}
          </span>
          <span className="text-neutral-700" aria-hidden="true">→</span>
          <span className="text-[10px] font-bold uppercase tracking-wider text-neutral-300 truncate">
            {afterLabel}
          </span>
          <span className="ml-3 text-[10px] font-mono text-emerald-400">+{stats.added}</span>
          <span className="text-[10px] font-mono text-red-400">−{stats.removed}</span>
        </div>
        <div role="radiogroup" aria-label="Diff view mode" className="flex gap-1">
          {['unified', 'split'].map((m) => (
            <button
              key={m}
              role="radio"
              aria-checked={mode === m}
              onClick={() => setMode(m)}
              className={`
                inline-flex items-center gap-1
                text-[10px] font-bold uppercase tracking-wider
                px-2 py-1 rounded-md border transition-colors
                ${mode === m
                  ? 'bg-white text-black border-white'
                  : 'border-[var(--border-subtle)] text-neutral-500 hover:text-white hover:border-[var(--border-default)]'}
              `}
            >
              {m === 'unified'
                ? <AlignJustify size={10} aria-hidden="true" />
                : <Columns size={10} aria-hidden="true" />}
              {m}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        {mode === 'unified' ? (
          <pre className="m-0 text-[11px] leading-relaxed font-mono">
            {diff.lines.map((l, idx) => (
              <div
                key={idx}
                className={`grid grid-cols-[3.25rem_3.25rem_1.25rem_1fr] ${ROW_STYLES[l.type]}`}
              >
                <span className="px-2 text-right text-neutral-700 select-none">{l.li ?? ''}</span>
                <span className="px-2 text-right text-neutral-700 select-none">{l.ri ?? ''}</span>
                <span className="px-1 text-center select-none opacity-80">{SIGIL[l.type]}</span>
                <span className="pr-3 whitespace-pre-wrap break-words">{l.type === 'add' ? l.b : l.a}</span>
              </div>
            ))}
          </pre>
        ) : (
          <pre className="m-0 text-[11px] leading-relaxed font-mono">
            {diff.lines.map((l, idx) => (
              <div key={idx} className="grid grid-cols-2 border-b border-[var(--border-subtle)] last:border-b-0">
                <div className={`grid grid-cols-[3.25rem_1fr] px-1 ${l.type !== 'add' ? ROW_STYLES[l.type === 'context' ? 'context' : 'del'] : 'bg-transparent text-neutral-700'}`}>
                  <span className="px-1 text-right text-neutral-700 select-none">{l.li ?? ''}</span>
                  <span className="pr-3 whitespace-pre-wrap break-words">{l.a}</span>
                </div>
                <div className={`grid grid-cols-[3.25rem_1fr] px-1 ${l.type !== 'del' ? ROW_STYLES[l.type === 'context' ? 'context' : 'add'] : 'bg-transparent text-neutral-700'}`}>
                  <span className="px-1 text-right text-neutral-700 select-none">{l.ri ?? ''}</span>
                  <span className="pr-3 whitespace-pre-wrap break-words">{l.b}</span>
                </div>
              </div>
            ))}
          </pre>
        )}
      </div>
    </div>
  )
}
