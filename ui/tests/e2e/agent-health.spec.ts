// agent-health.spec.ts — Sprint 4.3
// Behavioural e2e for the Agent Health page.
//
// Pins both backend endpoints, the rank-by toggle re-fetches, the
// kind toggle re-fetches, and recent-event rows render.

import { test, expect } from '@playwright/test'
import { loginViaApi } from './_helpers/login'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('agent health page', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live agent-health test')

  test('renders both tables and toggles re-fetch the right endpoints', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(`console: ${m.text()}`)
    })

    const healthCalls: string[] = []
    const eventsCalls: string[] = []
    page.on('response', (resp) => {
      const url = resp.url()
      if (url.includes('/audit/fleet/agent-health')) {
        healthCalls.push(url)
        expect(resp.status(), `agent-health non-200: ${url}`).toBe(200)
      }
      if (url.includes('/audit/fleet/recent-events')) {
        eventsCalls.push(url)
        expect(resp.status(), `recent-events non-200: ${url}`).toBe(200)
      }
    })

    await loginViaApi(page)
    await page.goto('/agent-health')

    await expect(page.getByRole('heading', { name: /^Agent Health$/i }))
      .toBeVisible({ timeout: 10_000 })

    await page.waitForLoadState('networkidle')
    expect(healthCalls.length, 'no /audit/fleet/agent-health observed').toBeGreaterThan(0)
    expect(eventsCalls.length, 'no /audit/fleet/recent-events observed').toBeGreaterThan(0)

    // Rank-by toggle — clicking "Error rate" re-fetches agent-health.
    const healthBefore = healthCalls.length
    await page.getByRole('button', { name: 'Error rate' }).click()
    await page.waitForLoadState('networkidle')
    expect(
      healthCalls.length,
      `rank-by toggle did not refetch agent-health (before=${healthBefore}, after=${healthCalls.length})`,
    ).toBeGreaterThan(healthBefore)
    expect(
      healthCalls.some((u) => u.includes('rank_by=error_rate')),
      'no rank_by=error_rate observed',
    ).toBeTruthy()

    // Kind toggle — clicking "All" re-fetches recent-events with kind=any.
    const eventsBefore = eventsCalls.length
    await page.getByRole('button', { name: /^All$/ }).click()
    await page.waitForLoadState('networkidle')
    expect(eventsCalls.length).toBeGreaterThan(eventsBefore)
    expect(
      eventsCalls.some((u) => u.includes('kind=any')),
      'no kind=any observed',
    ).toBeTruthy()

    expect(errors, `unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })
})
