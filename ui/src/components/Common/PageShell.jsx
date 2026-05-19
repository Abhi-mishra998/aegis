import React from 'react'

/**
 * PageShell — standard top-of-page wrapper.
 *
 *   <PageShell
 *     title="Policies"
 *     description="Author and version OPA bundles."
 *     actions={<button className="btn-premium ...">+ Add rule</button>}
 *     breadcrumbs={[{ label: 'Console' }, { label: 'Policies' }]}
 *   >
 *     <PageSection>...</PageSection>
 *   </PageShell>
 *
 * The header is responsive: title block + actions stack on small screens and
 * sit side-by-side on lg+. Actions wrap rather than overflow.
 */
export default function PageShell({
  title,
  description,
  breadcrumbs,
  actions,
  children,
  className = '',
}) {
  return (
    <div className={`flex flex-col gap-6 lg:gap-8 ${className}`}>
      {(title || actions || breadcrumbs) && (
        <header className="flex flex-col gap-4">
          {breadcrumbs?.length ? (
            <nav aria-label="Breadcrumb">
              <ol className="flex flex-wrap items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-neutral-500 font-medium">
                {breadcrumbs.map((crumb, i) => (
                  <li key={`${crumb.label}-${i}`} className="flex items-center gap-1.5">
                    {i > 0 && <span aria-hidden="true" className="text-neutral-700">/</span>}
                    {crumb.href ? (
                      <a href={crumb.href} className="hover:text-white transition-colors">
                        {crumb.label}
                      </a>
                    ) : (
                      <span className={i === breadcrumbs.length - 1 ? 'text-neutral-300' : ''}>
                        {crumb.label}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </nav>
          ) : null}

          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between lg:gap-6">
            <div className="min-w-0 flex flex-col gap-1.5">
              {title && (
                <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
                  {title}
                </h1>
              )}
              {description && (
                <p className="text-sm text-neutral-400 leading-relaxed max-w-2xl">
                  {description}
                </p>
              )}
            </div>

            {actions && (
              <div className="flex flex-wrap items-center gap-2 lg:shrink-0">
                {actions}
              </div>
            )}
          </div>
        </header>
      )}

      <div className="flex flex-col gap-6 lg:gap-8 min-w-0">
        {children}
      </div>
    </div>
  )
}

/**
 * PageSection — a labelled container for a stand-alone block on a page.
 * Use inside <PageShell>. Renders title row + optional actions + content.
 */
export function PageSection({
  title,
  description,
  actions,
  children,
  className = '',
  bodyClassName = '',
}) {
  return (
    <section className={`flex flex-col gap-4 min-w-0 ${className}`}>
      {(title || actions) && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between sm:gap-4">
          <div className="min-w-0 flex flex-col gap-1">
            {title && (
              <h2 className="text-sm font-bold text-white uppercase tracking-[0.12em]">
                {title}
              </h2>
            )}
            {description && (
              <p className="text-xs text-neutral-500 leading-relaxed">
                {description}
              </p>
            )}
          </div>
          {actions && (
            <div className="flex flex-wrap items-center gap-2 sm:shrink-0">
              {actions}
            </div>
          )}
        </div>
      )}
      <div className={`min-w-0 ${bodyClassName}`}>{children}</div>
    </section>
  )
}
