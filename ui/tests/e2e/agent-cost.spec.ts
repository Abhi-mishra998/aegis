// agent-cost.spec.ts — Sprint 4.4
// Behavioural e2e for the Agent FinOps burn-down page.
//
// Pins that the burn-down endpoint is hit, the page surfaces the
// canonical "tenant / agent" labels, and the ?agent_id= deep-link is
// honored end-to-end.

import { test, expect } from '@playwright/test'
import { loginViaApi } from './_helpers/login'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('agent FinOps burn-down', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live agent-cost test')

  test('renders gauges and calls /usage/fleet/burn-down', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(`console: ${m.text()}`)
    })

    const burnCalls: string[] = []
    page.on('response', (resp) => {
      const url = resp.url()
      if (url.includes('/usage/fleet/burn-down')) {
        burnCalls.push(url)
        expect(resp.status(), `burn-down non-200: ${url}`).toBe(200)
      }
    })

    await loginViaApi(page)
    await page.goto('/agent-cost')

    await expect(page.getByRole('heading', { name: /Agent FinOps/i }))
      .toBeVisible({ timeout: 10_000 })

    // Two panels — Tenant and Agent (Agent shows "no agent_id passed" when empty).
    await expect(page.getByRole('heading', { name: /^Tenant$/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: /^Agent$/  })).toBeVisible()

    await page.waitForLoadState('networkidle')
    expect(burnCalls.length, 'no /usage/fleet/burn-down observed').toBeGreaterThan(0)

    // Deep-link with a synthetic agent_id — the page must re-fetch.
    const before = burnCalls.length
    await page.goto('/agent-cost?agent_id=00000000-0000-0000-0000-000000000099')
    await page.waitForLoadState('networkidle')
    expect(burnCalls.length).toBeGreaterThan(before)
    expect(
      burnCalls.some((u) => u.includes('agent_id=00000000-0000-0000-0000-000000000099')),
      `no agent_id propagated to backend; calls: ${burnCalls.join('\n')}`,
    ).toBeTruthy()

    expect(errors, `unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })
})
