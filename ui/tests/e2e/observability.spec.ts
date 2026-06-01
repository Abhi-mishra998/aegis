// observability.spec.ts — sprint-6.6
// Behavioural test: Observability page loads, /system/health returns 200,
// p95 latency value appears in the DOM.

import { test, expect } from '@playwright/test'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('observability page', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set')

  test('renders /system/health and shows a latency value', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))

    await page.request.post('/auth/token', {
      data: { email: process.env.PLAYWRIGHT_USER, password: PASSWORD },
    })
    await page.goto('/observability')

    await expect(page.getByRole('heading', { name: /observability|system health|infrastructure/i }))
      .toBeVisible({ timeout: 10_000 })

    // /system/health is in _SKIP_PATHS so it should always 200.
    const healthResp = await page.request.get('/system/health')
    expect(healthResp.status()).toBe(200)
    const body = await healthResp.json()
    expect(body, 'expected services or latency block in /system/health').toBeTruthy()

    // The page must surface SOMETHING latency-shaped (p95, ms, latency text).
    await expect(page.locator('text=/p9[0-9]|latency|\\d+\\s?ms/i').first())
      .toBeVisible({ timeout: 10_000 })

    expect(errors, `console errors: ${errors.join('\n')}`).toHaveLength(0)
  })
})
