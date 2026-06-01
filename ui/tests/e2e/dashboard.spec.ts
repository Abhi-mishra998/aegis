// dashboard.spec.ts — sprint-3.2
// Behavioural test for ExecutiveDashboard's sprint-2.9 degraded-state banner.
// String-match contracts in tests/test_phase*_ui.py only verified that the
// substring "Live aggregate unavailable" appeared in the JSX source; this
// test verifies the banner ACTUALLY RENDERS when the backend is unhealthy.

import { test, expect } from '@playwright/test'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('executive dashboard', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live dashboard test')

  test('renders without console errors on happy path', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })

    // Cookie-based login via API (faster than UI login for every test).
    await page.request.post('/auth/token', {
      data: { email: process.env.PLAYWRIGHT_USER, password: PASSWORD },
    })
    await page.goto('/dashboard')

    await expect(page.getByRole('heading', { name: /executive overview/i }))
      .toBeVisible({ timeout: 10_000 })

    // Happy-path: the degraded banner must NOT be visible.
    await expect(page.getByText(/live aggregate unavailable/i)).not.toBeVisible()
    await expect(page.getByText(/all backend sources unreachable/i)).not.toBeVisible()

    expect(errors, `console errors: ${errors.join('\n')}`).toHaveLength(0)
  })

  test('degraded-state banner appears when /dashboard/state returns 5xx', async ({
    page,
  }) => {
    // Intercept /dashboard/state and force a failure so we can prove the
    // sprint-2.9 fallback actually surfaces the degraded state.
    await page.route('**/dashboard/state', (route) => route.fulfill({ status: 503 }))

    await page.request.post('/auth/token', {
      data: { email: process.env.PLAYWRIGHT_USER, password: PASSWORD },
    })
    await page.goto('/dashboard')

    await expect(page.getByText(/live aggregate unavailable|all backend sources unreachable/i))
      .toBeVisible({ timeout: 10_000 })
  })
})
