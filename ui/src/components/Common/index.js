// Barrel for the enterprise primitives. Import as:
//   import { Modal, ConfirmDialog, PageShell, PageSection, EmptyState, SectionHeader } from '@/components/Common'
//
// Keeps page-level imports flat and removes the "deep import" sprawl across
// the routes folder.

export { default as Modal }              from './Modal'
export { default as ConfirmDialog }      from './ConfirmDialog'
export { default as PageShell, PageSection } from './PageShell'
export { default as SectionHeader }      from './SectionHeader'
export { default as EmptyState }         from './EmptyState'
export { default as Toast }              from './Toast'
export { default as Card }               from './Card'
export { default as Button }             from './Button'
export { default as DataTable }          from './DataTable'
export { default as SkeletonLoader }     from './SkeletonLoader'
// Sprint 4 — enterprise UX primitives
export { default as KeyboardCheatsheet } from './KeyboardCheatsheet'
export { default as ActivityFeed }       from './ActivityFeed'
export { default as InvestigationLayout } from './InvestigationLayout'
export { default as DiffViewer }         from './DiffViewer'
export { default as LiveKpiTile }        from './LiveKpiTile'
