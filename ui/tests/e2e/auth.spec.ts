// auth.spec.ts — sprint-3.2
// Replaces the string-match assertions in tests/test_phase*_ui.py for the
// login flow with a real browser-driven check.
//
// Pre-conditions for local run:
//   docker compose -f infra/docker-compose.yml up -d
//   PLAYWRIGHT_USER=admin@aegisagent.in PLAYWRIGHT_PASSWORD=... npm run test:e2e

import { test, expect } from '@playwright/test'

const USER     = process.env.PLAYWRIGHT_USER     || 'admin@aegisagent.in'
const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('auth', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live login test')

  test('login → cookie → dashboard renders', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel(/email/i).fill(USER)
    await page.getByLabel(/password/i).fill(PASSWORD)
    await page.getByRole('button', { name: /sign in|log in/i }).click()

    // Successful auth lands us somewhere other than /login.
    await expect(page).not.toHaveURL(/\/login$/, { timeout: 10_000 })

    // acp_token cookie must be set (HttpOnly so we can only check existence).
    const cookies = await page.context().cookies()
    expect(cookies.some((c) => c.name === 'acp_token')).toBeTruthy()

    // Executive dashboard must show its heading without a JavaScript crash.
    await expect(page.getByRole('heading', { name: /executive overview/i }))
      .toBeVisible({ timeout: 10_000 })
  })

  test('bad password rejected', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel(/email/i).fill('does-not-exist@aegisagent.in')
    await page.getByLabel(/password/i).fill('wrong')
    await page.getByRole('button', { name: /sign in|log in/i }).click()

    await expect(page.getByText(/invalid|incorrect|failed/i)).toBeVisible({
      timeout: 5_000,
    })
  })
})
