import React from 'react'

/**
 * SectionHeader — small uppercase header used inside cards / panels.
 *
 *   <SectionHeader title="Top risky agents" actions={<button>View all</button>} />
 *
 * Keeps spacing + typography consistent across pages so dashboards no longer
 * drift across cards.
 */
export default function SectionHeader({
  title,
  description,
  actions,
  icon,
  className = '',
}) {
  return (
    <div className={`flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4 ${className}`}>
      <div className="min-w-0 flex flex-col gap-1">
        <div className="flex items-center gap-2 min-w-0">
          {icon && <span className="text-neutral-500 shrink-0" aria-hidden="true">{icon}</span>}
          <h3 className="text-[11px] font-bold uppercase tracking-[0.15em] text-white truncate">
            {title}
          </h3>
        </div>
        {description && (
          <p className="text-xs text-neutral-500 leading-relaxed">{description}</p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2 sm:shrink-0">
          {actions}
        </div>
      )}
    </div>
  )
}
