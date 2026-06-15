// fleet.spec.ts — Sprint 4
// Behavioural e2e for the Fleet Home dashboard.
//
// Pins:
//   * page renders without pageerror / console errors
//   * GET /audit/fleet/kpis is hit and returns 200
//   * GET /audit/fleet/timeseries is hit and returns 200
//   * KPI card labels render (Decisions / Deny rate / Error rate / Active agents)
//   * metric toggle (Decisions → Denied) issues a fresh /timeseries call
//   * window selector (Last 1h → Last 24h) re-fetches both KPIs and series
//
// Run:
//   PLAYWRIGHT_USER=admin@aegisagent.in \
//     PLAYWRIGHT_PASSWORD=... \
//     AEGIS_BASE_URL=http://localhost:5173 \
//     npm run test:e2e -- fleet.spec.ts

import { test, expect } from '@playwright/test'
import { loginViaApi } from './_helpers/login'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('fleet dashboard', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live fleet test')

  test('renders KPI cards and time-series; metric + window toggles re-fetch', async ({ page }) => {
    // Boot-time invariants the page must not violate.
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(`console: ${m.text()}`)
    })

    // Watch the two backend endpoints the Fleet page consumes.
    const kpiCalls: string[] = []
    const tsCalls: string[] = []
    page.on('response', (resp) => {
      const url = resp.url()
      if (url.includes('/audit/fleet/kpis')) {
        kpiCalls.push(url)
        expect(resp.status(), `kpis non-200: ${url}`).toBe(200)
      }
      if (url.includes('/audit/fleet/timeseries')) {
        tsCalls.push(url)
        expect(resp.status(), `timeseries non-200: ${url}`).toBe(200)
      }
    })

    await loginViaApi(page)
    await page.goto('/fleet')

    // Heading must surface.
    await expect(page.getByRole('heading', { name: /^Fleet$/i }))
      .toBeVisible({ timeout: 10_000 })

    // KPI card labels — the dashboard's headline contract. The card
    // labels live in <span> elements; "Decisions" doubles as a metric-
    // toggle <button> below the cards, so we scope the locator to span
    // to disambiguate.
    for (const label of ['Decisions', 'Deny rate', 'Error rate', 'Active agents']) {
      await expect(
        page.locator('span').filter({ hasText: new RegExp(`^${label}$`) }),
      ).toBeVisible({ timeout: 10_000 })
    }

    // Wait for the initial fetch wave to land.
    await page.waitForLoadState('networkidle')
    expect(kpiCalls.length, 'no /audit/fleet/kpis observed').toBeGreaterThan(0)
    expect(tsCalls.length, 'no /audit/fleet/timeseries observed').toBeGreaterThan(0)
    const kpisBefore = kpiCalls.length
    const tsBefore = tsCalls.length

    // Metric toggle: click "Denied" — must fire a fresh /timeseries call
    // (KPI cards don't re-query because they're metric-agnostic).
    await page.getByRole('button', { name: 'Denied' }).click()
    await page.waitForLoadState('networkidle')
    expect(
      tsCalls.length,
      `metric toggle did not refetch /audit/fleet/timeseries (before=${tsBefore}, after=${tsCalls.length})`,
    ).toBeGreaterThan(tsBefore)
    // The new URL must carry metric=denied.
    expect(tsCalls.some((u) => u.includes('metric=denied')), `no metric=denied call: ${tsCalls.join('\n')}`).toBeTruthy()

    // Window selector: change to "Last 24h" — re-fetches BOTH endpoints.
    await page.locator('select').first().selectOption({ label: 'Last 24h' })
    await page.waitForLoadState('networkidle')
    expect(
      kpiCalls.length,
      `window selector did not refetch /audit/fleet/kpis (before=${kpisBefore}, after=${kpiCalls.length})`,
    ).toBeGreaterThan(kpisBefore)
    expect(
      kpiCalls.some((u) => u.includes('window_minutes=1440')),
      'no window_minutes=1440 in any KPI call',
    ).toBeTruthy()

    // Cross-page link: "Open Agent Health" must navigate.
    await page.getByRole('link', { name: /Open Agent Health/i }).click()
    await expect(page.getByRole('heading', { name: /^Agent Health$/i }))
      .toBeVisible({ timeout: 10_000 })

    expect(errors, `unexpected console / page errors:\n${errors.join('\n')}`).toHaveLength(0)
  })
})
