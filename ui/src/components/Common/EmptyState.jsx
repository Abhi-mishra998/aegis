import React from 'react'

/**
 * EmptyState — used when a table/list/section has no rows yet.
 *
 *   <EmptyState
 *     icon={<Shield size={28} />}
 *     title="No policies yet"
 *     description="Author a rule to start enforcing runtime denies."
 *     action={<button className="btn-premium ...">+ Add rule</button>}
 *   />
 *
 * Centered, responsive, dark-mode safe. Replaces ad-hoc "no data" divs that
 * lived inside many pages with inconsistent spacing.
 */
export default function EmptyState({
  icon,
  title,
  description,
  action,
  className = '',
  size = 'md',
}) {
  const padding = size === 'sm' ? 'py-10' : size === 'lg' ? 'py-20' : 'py-14'
  return (
    <div
      role="status"
      className={`
        ${padding}
        flex flex-col items-center justify-center text-center
        px-6 gap-3
        border border-dashed border-[var(--border-default)]
        bg-[var(--bg-surface)]/40
        rounded-2xl
        ${className}
      `}
    >
      {icon && (
        <div className="w-12 h-12 rounded-full bg-white/[0.04] border border-white/[0.06] flex items-center justify-center text-neutral-400">
          {icon}
        </div>
      )}
      {title && (
        <h3 className="text-sm font-semibold text-white tracking-tight">
          {title}
        </h3>
      )}
      {description && (
        <p className="text-xs text-neutral-400 max-w-md leading-relaxed">
          {description}
        </p>
      )}
      {action && <div className="mt-2 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}
