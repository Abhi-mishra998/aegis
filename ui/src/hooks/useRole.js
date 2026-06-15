import { useAuth } from './useAuth'

// Sprint 1 — canonical role vocabulary mirrors sdk/common/roles.py.
// Legacy values (ADMIN/SECURITY/AUDITOR/VIEWER) projected onto the
// canonical 5-tier vocab so UI gates work identically for new + old rows.
const LEGACY_TO_CANONICAL = {
  OWNER: 'OWNER',
  ADMIN: 'ADMIN',
  SECURITY_ANALYST: 'SECURITY_ANALYST',
  DEVELOPER: 'DEVELOPER',
  READ_ONLY: 'READ_ONLY',
  SECURITY: 'SECURITY_ANALYST',
  AUDITOR: 'READ_ONLY',
  VIEWER: 'READ_ONLY',
  AGENT: 'DEVELOPER',
}

export function useRole() {
  const { role } = useAuth()
  const raw = (role || '').toUpperCase()
  const canonical = LEGACY_TO_CANONICAL[raw] || 'READ_ONLY'

  const isOwner = canonical === 'OWNER'
  const isAdmin = canonical === 'ADMIN' || isOwner
  const isSecurityAnalyst = canonical === 'SECURITY_ANALYST'
  const isDeveloper = canonical === 'DEVELOPER'
  const isReadOnly = canonical === 'READ_ONLY'

  return {
    role: canonical,
    rawRole: raw,
    isOwner,
    isAdmin,
    isSecurityAnalyst,
    isDeveloper,
    isReadOnly,
    // Legacy aliases (preserved so existing call sites keep compiling).
    isAuditor: isReadOnly,
    isViewer: isReadOnly,
    canMutate: isOwner || isAdmin,
    canViewKillSwitch: isOwner || isAdmin,
    canExitShadowMode: isOwner,
  }
}
