export const AUTH_EVENTS = {
  FAILURE: 'acp:auth:failure',
}

const REASON_LABELS = {
  session_expired:  'Session Expired',
  unauthorized:     'Unauthorized Access',
  token_invalid:    'Token Invalid',
  token_revoked:    'Token Revoked',
  csrf_failure:     'CSRF Validation Failed',
}

export function emitAuthFailure({ reason = 'unauthorized', url, statusCode } = {}) {
  window.dispatchEvent(
    new CustomEvent(AUTH_EVENTS.FAILURE, {
      detail: {
        incidentId: crypto.randomUUID(),
        reason,
        reasonLabel: REASON_LABELS[reason] ?? REASON_LABELS.unauthorized,
        url:         url ?? window.location.pathname,
        statusCode:  statusCode ?? null,
        timestamp:   new Date().toISOString(),
      },
    })
  )
}

export function onAuthFailure(handler) {
  window.addEventListener(AUTH_EVENTS.FAILURE, handler)
  return () => window.removeEventListener(AUTH_EVENTS.FAILURE, handler)
}
