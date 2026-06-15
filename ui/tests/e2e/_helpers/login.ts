// _helpers/login.ts — Sprint 4
//
// Shared login helper for the Sprint 4 e2e specs.
//
// Drives the login through ``page.context().request.post`` so the
// resulting Set-Cookie lands in the SAME cookie jar the subsequent
// ``page.goto`` reads from. The previous form-driven approach was
// failing intermittently with "Failed to fetch" — most likely a
// browser-process race with Vite's HTTPS upstream proxy under
// chromium-headless-shell. The request-context path bypasses the form
// entirely so the spec under test only exercises the page it cares
// about, not the login flow.
//
// localStorage state required by App.jsx::readSessionState
// (``tenant_id`` + ``acp_token_expiry``) is seeded via
// ``addInitScript`` so it lands BEFORE any page script runs — setting
// it after navigation races with the redirect to /login.

import type { Page } from '@playwright/test'

export const DEFAULT_DEMO_TENANT = '00000000-0000-0000-0000-000000000001'

export async function loginViaApi(page: Page): Promise<void> {
  const email    = process.env.PLAYWRIGHT_USER     || 'admin@aegisagent.in'
  const password = process.env.PLAYWRIGHT_PASSWORD || ''
  const tenantId = process.env.PLAYWRIGHT_TENANT_ID || DEFAULT_DEMO_TENANT
  if (!password) {
    throw new Error('PLAYWRIGHT_PASSWORD must be set before calling loginViaApi')
  }

  // The login endpoint lives on the gateway. When the e2e runs against
  // a local Vite that's been configured to point the SPA's fetches at
  // dev.aegisagent.in via VITE_GATEWAY_URL, the request-context login
  // must also use that absolute URL — otherwise it would hit Vite's
  // own server (which 404s on /auth/token).
  const apiBase = process.env.AEGIS_API_URL || ''
  const url = apiBase ? `${apiBase.replace(/\/$/, '')}/auth/token` : '/auth/token'
  const resp = await page.context().request.post(url, {
    data:    { email, password },
    headers: { 'X-Tenant-ID': tenantId, 'Content-Type': 'application/json' },
  })
  if (!resp.ok()) {
    const body = await resp.text()
    throw new Error(
      `auth/token failed: HTTP ${resp.status()} — body: ${body.slice(0, 400)}`,
    )
  }

  // Seed the SPA's localStorage entries that App.jsx::readSessionState
  // checks BEFORE any page navigates. ``acp_token_expiry`` must be a
  // future epoch-ms; the JWT itself is good for 1 hour, so 50 minutes is
  // a safe in-test margin.
  const expiry = Date.now() + 50 * 60 * 1000
  await page.context().addInitScript((args) => {
    window.localStorage.setItem('tenant_id', args.tid)
    window.localStorage.setItem('acp_token_expiry', String(args.exp))
  }, { tid: tenantId, exp: expiry })
}
