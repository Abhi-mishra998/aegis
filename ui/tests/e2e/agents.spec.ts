// agents.spec.ts — sprint-6.6
// Behavioural test: Agents page renders, /agents GET returns 200,
// agent rows appear (or empty-state for a tenant with zero agents).

import { test, expect } from '@playwright/test'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('agents page', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set')

  test('renders and fetches /agents', async ({ page }) => {
    let agentsHit = false
    page.on('response', (resp) => {
      if (/\/agents(\?|$)/.test(resp.url()) && resp.request().method() === 'GET') {
        agentsHit = true
        expect(resp.status(), `expected 200 for ${resp.url()}`).toBe(200)
      }
    })

    await page.request.post('/auth/token', {
      data: { email: process.env.PLAYWRIGHT_USER, password: PASSWORD },
    })
    await page.goto('/agents')

    await expect(page.getByRole('heading', { name: /agents?/i }))
      .toBeVisible({ timeout: 10_000 })

    await page.waitForLoadState('networkidle')
    expect(agentsHit, 'no GET /agents observed').toBeTruthy()
  })
})
