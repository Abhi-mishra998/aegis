import React from 'react'
import Button from './Button'

export default function EmptyStateV2({
  icon: Icon,
  title,
  body,
  ctaLabel,
  onCta,
  secondaryCtaLabel,
  onSecondaryCta,
}) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-10 text-center">
      {Icon && <Icon size={36} className="mx-auto mb-3 text-neutral-600" aria-hidden="true" />}
      <h3 className="text-sm font-semibold text-neutral-200 mb-1">{title}</h3>
      {body && <p className="text-xs text-neutral-500 mb-4 max-w-md mx-auto">{body}</p>}
      {ctaLabel && onCta && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <Button variant="primary" size="sm" onClick={onCta}>{ctaLabel}</Button>
          {secondaryCtaLabel && onSecondaryCta && (
            <Button variant="ghost" size="sm" onClick={onSecondaryCta}>{secondaryCtaLabel}</Button>
          )}
        </div>
      )}
    </div>
  )
}
