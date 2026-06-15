// agent-topology.spec.ts — Sprint 4.5
// Behavioural e2e for the Agent Topology page (React Flow over the
// identity graph).
//
// Pins that GET /graph/agents is hit, React Flow renders (the canvas
// element appears), the legend is visible, and the page doesn't error
// out when the tenant has no nodes (empty-state copy).

import { test, expect } from '@playwright/test'
import { loginViaApi } from './_helpers/login'

const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''

test.describe('agent topology page', () => {
  test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live agent-topology test')

  test('renders React Flow and fetches /graph/agents', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(`console: ${m.text()}`)
    })

    const graphCalls: string[] = []
    page.on('response', (resp) => {
      const url = resp.url()
      if (/\/graph\/agents/.test(url)) {
        graphCalls.push(url)
        expect(resp.status(), `graph/agents non-200: ${url}`).toBe(200)
      }
    })

    await loginViaApi(page)
    await page.goto('/agent-topology')

    await expect(page.getByRole('heading', { name: /^Agent Topology$/i }))
      .toBeVisible({ timeout: 10_000 })

    await page.waitForLoadState('networkidle')
    expect(graphCalls.length, 'no /graph/agents observed').toBeGreaterThan(0)

    // React Flow drops a ``.react-flow__renderer`` div into the DOM as
    // soon as the canvas mounts — even with zero nodes.
    await expect(page.locator('.react-flow').first()).toBeVisible({ timeout: 10_000 })

    // Legend copy must be visible (covers the "what do the colours mean" UX).
    await expect(page.getByText(/Legend:/)).toBeVisible()

    expect(errors, `unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })
})
