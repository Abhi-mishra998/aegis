import { useAgents } from '../../hooks/useAgents'

/**
 * AgentScopePicker — shared agent-selector pulled out of Sidebar.jsx and
 * Topbar.jsx in the sprint-5 audit cleanup. Both call sites duplicated
 * the same three-state render (loading / empty / select) with
 * almost-identical markup; the only real differences are font size,
 * border radius, container constraints, and the option-label format.
 *
 * Variants:
 *   compact  — Sidebar shape: text-[10px|11px], rounded-md, flex-1.
 *              Option label = agent name only.
 *   header   — Topbar shape:  text-xs, rounded-lg, max-w-[200px].
 *              Option label = "{name} · {status}".
 *
 * Pass a custom `formatOption(a)` for one-off label tweaks; defaults to
 * the variant convention. Pass `loadingText` to override the default
 * placeholder.
 */

// Shared dark-theme select+option styling moved from inline `style={}` to
// Tailwind arbitrary-value utilities so axe/Lighthouse no longer flags the
// inline color literals. WebkitTextFillColor and colorScheme are expressed
// as Tailwind arbitrary-property classes ([-webkit-text-fill-color:…],
// [color-scheme:dark]) which compile to the same CSS at build time.
const SELECT_THEME_CLASS = 'text-[#d4d4d4] [-webkit-text-fill-color:#d4d4d4] [color-scheme:dark]'
const OPTION_THEME_CLASS = 'bg-[#111] text-white'

const VARIANT = {
  compact: {
    placeholderClass: 'text-[10px] text-neutral-600 font-mono truncate',
    selectClass: (
      'flex-1 min-w-0 text-[11px] font-mono '
      + 'bg-[var(--bg-surface-elevated)] '
      + 'border border-[var(--border-subtle)] '
      + 'rounded-md px-1.5 py-1 '
      + 'focus:outline-none focus:border-white/30 '
      + 'cursor-pointer truncate '
      + SELECT_THEME_CLASS
    ),
    formatOption: (a) => a.name,
    loadingText: 'Loading…',
  },
  header: {
    placeholderClass: 'text-xs text-neutral-600 font-mono',
    selectClass: (
      'text-xs font-mono '
      + 'bg-[var(--bg-surface-elevated)] '
      + 'border border-[var(--border-subtle)] '
      + 'rounded-lg px-2 py-1 '
      + 'focus:outline-none focus:border-white/30 '
      + 'transition-colors cursor-pointer '
      + 'max-w-[200px] truncate '
      + SELECT_THEME_CLASS
    ),
    formatOption: (a) => `${a.name} · ${(a.status || 'unknown').toLowerCase()}`,
    loadingText: 'Loading agents…',
  },
}

export default function AgentScopePicker({
  variant = 'compact',
  formatOption,
  loadingText,
}) {
  const v = VARIANT[variant] || VARIANT.compact
  const { agents, agentsLoading, selectedAgentId, setSelectedAgentId } = useAgents()
  const label = formatOption || v.formatOption
  const loading = loadingText ?? v.loadingText

  if (agentsLoading) {
    return <span className={v.placeholderClass}>{loading}</span>
  }
  if (!agents || agents.length === 0) {
    return <span className={`${v.placeholderClass} italic`}>No agents</span>
  }
  return (
    <select name="select"
      value={selectedAgentId || ''}
      onChange={(e) => setSelectedAgentId(e.target.value)}
      aria-label="Select active agent"
      className={v.selectClass}
    >
      {agents.map((a) => (
        <option key={a.id} value={a.id} className={OPTION_THEME_CLASS}>
          {label(a)}
        </option>
      ))}
    </select>
  )
}
