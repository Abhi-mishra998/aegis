import React, { useState } from 'react'
import { ChevronLeft, ChevronRight, PanelLeftClose, PanelRightClose, X } from 'lucide-react'

/**
 * InvestigationLayout — 3-pane workspace primitive used by audit / incident /
 * forensics / flight-recorder views.
 *
 *   ┌─────────┬────────────────────┬─────────────┐
 *   │ FILTERS │      LIST          │   DETAIL    │
 *   │ (left)  │   (center)         │   (right)   │
 *   └─────────┴────────────────────┴─────────────┘
 *
 * Behavior:
 *   - Left and right panels can be collapsed independently
 *   - Detail pane slides in when an item is selected; closing it returns
 *     focus to the table list
 *   - At sm/md breakpoints the detail pane becomes a fullscreen overlay
 *     (responsive without nuking the layout)
 *
 *   <InvestigationLayout
 *     filters={<Filters />}
 *     list={<AuditTable onSelect={setRow} />}
 *     detail={row ? <AuditDetail row={row} onClose={() => setRow(null)} /> : null}
 *   />
 */
export default function InvestigationLayout({
  filters,
  list,
  detail,
  detailTitle,
  onDetailClose,
  className = '',
}) {
  const [leftCollapsed, setLeftCollapsed]   = useState(false)
  const hasDetail = !!detail

  return (
    <div
      className={`
        flex flex-col lg:flex-row gap-4 min-w-0
        h-[calc(100dvh-12rem)] min-h-[28rem]
        ${className}
      `}
    >
      {/* LEFT — filters */}
      {filters && (
        <aside
          className={`
            shrink-0 flex flex-col min-h-0
            bg-[var(--bg-surface)] border border-[var(--border-subtle)]
            rounded-2xl overflow-hidden
            transition-[width] duration-200 ease-out
            ${leftCollapsed ? 'w-12' : 'w-full lg:w-72'}
          `}
          aria-label="Filters"
        >
          <div className="px-3 py-2 border-b border-[var(--border-subtle)] flex items-center justify-between gap-2 shrink-0">
            <span
              className={`
                text-[10px] font-bold uppercase tracking-[0.15em] text-white truncate
                ${leftCollapsed ? 'sr-only' : ''}
              `}
            >
              Filters
            </span>
            <button
              type="button"
              onClick={() => setLeftCollapsed((v) => !v)}
              aria-label={leftCollapsed ? 'Expand filters' : 'Collapse filters'}
              className="
                p-1 rounded-md text-neutral-500 hover:text-white hover:bg-white/[0.05]
                focus-visible:ring-1 focus-visible:ring-white/30
                transition-colors
              "
            >
              {leftCollapsed ? <ChevronRight size={13} /> : <PanelLeftClose size={13} />}
            </button>
          </div>
          {!leftCollapsed && (
            <div className="flex-1 min-h-0 overflow-y-auto p-3">{filters}</div>
          )}
        </aside>
      )}

      {/* CENTER — list */}
      <main className="flex-1 min-w-0 min-h-0 flex flex-col bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl overflow-hidden">
        <div className="flex-1 min-h-0 overflow-y-auto">{list}</div>
      </main>

      {/* RIGHT — detail (responsive: overlay on small screens) */}
      {hasDetail && (
        <>
          {/* Mobile/tablet overlay backdrop */}
          <div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
            onClick={onDetailClose}
            aria-hidden="true"
          />
          <aside
            className={`
              fixed lg:static inset-y-0 right-0 z-50 lg:z-auto
              w-full sm:w-[420px] lg:w-[480px] shrink-0
              flex flex-col min-h-0
              bg-[var(--bg-surface-elevated)] border-l border-[var(--border-default)]
              lg:border lg:border-[var(--border-subtle)] lg:rounded-2xl
              overflow-hidden
              animate-slide-down
            `}
            role="complementary"
            aria-label={detailTitle || 'Selection detail'}
          >
            <div className="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between gap-3 shrink-0">
              <span className="text-[11px] font-bold uppercase tracking-[0.15em] text-white truncate">
                {detailTitle || 'Detail'}
              </span>
              <button
                type="button"
                onClick={onDetailClose}
                aria-label="Close detail"
                className="
                  p-1.5 -m-1.5 rounded-md
                  text-neutral-500 hover:text-white hover:bg-white/[0.05]
                  focus-visible:ring-2 focus-visible:ring-white/30
                  transition-colors
                "
              >
                <X size={15} />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto p-4">{detail}</div>
          </aside>
        </>
      )}
    </div>
  )
}
