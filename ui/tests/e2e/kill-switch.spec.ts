// kill-switch.spec.ts — sprint-3.2
// Verifies the sprint-1 cross-tenant kill-switch fix at the HTTP boundary.
// A SECURITY user in Tenant A trying to engage Tenant B's kill switch must
// receive 403 from the gateway — not 200 with the kill switch applied.

import { test, expect } from '@playwright/test'

const TENANT_A_JWT  = process.env.PLAYWRIGHT_TENANT_A_SECURITY_JWT || ''
const TENANT_B_UUID = process.env.PLAYWRIGHT_TENANT_B_UUID || ''

test.describe('cross-tenant kill-switch protection (sprint-1 fix)', () => {
  test.skip(
    !TENANT_A_JWT || !TENANT_B_UUID,
    'PLAYWRIGHT_TENANT_A_SECURITY_JWT + PLAYWRIGHT_TENANT_B_UUID required',
  )

  test('POST kill-switch on another tenant returns 403', async ({ request }) => {
    const res = await request.post(`/decision/kill-switch/${TENANT_B_UUID}`, {
      headers: { Authorization: `Bearer ${TENANT_A_JWT}` },
      data: { action: 'engage' },
      failOnStatusCode: false,
    })
    expect(res.status()).toBe(403)
    const body = await res.json().catch(() => ({}))
    expect(JSON.stringify(body)).toMatch(/cannot operate.*different tenant/i)
  })

  test('DELETE kill-switch on another tenant returns 403', async ({ request }) => {
    const res = await request.delete(`/decision/kill-switch/${TENANT_B_UUID}`, {
      headers: { Authorization: `Bearer ${TENANT_A_JWT}` },
      failOnStatusCode: false,
    })
    expect(res.status()).toBe(403)
  })

  test('GET kill-switch on another tenant returns 403', async ({ request }) => {
    const res = await request.get(`/decision/kill-switch/${TENANT_B_UUID}`, {
      headers: { Authorization: `Bearer ${TENANT_A_JWT}` },
      failOnStatusCode: false,
    })
    expect(res.status()).toBe(403)
  })
})
