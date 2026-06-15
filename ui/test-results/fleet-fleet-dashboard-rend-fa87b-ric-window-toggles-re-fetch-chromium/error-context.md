# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: fleet.spec.ts >> fleet dashboard >> renders KPI cards and time-series; metric + window toggles re-fetch
- Location: tests/e2e/fleet.spec.ts:26:3

# Error details

```
Error: kpis non-200: https://dev.aegisagent.in/audit/fleet/kpis?window_minutes=180

expect(received).toBe(expected) // Object.is equality

Expected: 200
Received: 401
```

```
Error: page.waitForLoadState: Test ended.
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - generic [ref=e5]:
    - generic [ref=e6]:
      - img [ref=e8]
      - generic [ref=e10]:
        - heading "AgentControl" [level=1] [ref=e11]
        - paragraph [ref=e12]: Tamper-evident replay + runtime deny for AI agents.
    - generic [ref=e13]:
      - generic [ref=e14]:
        - generic [ref=e15]:
          - generic [ref=e16]: Email
          - textbox "Email" [ref=e17]:
            - /placeholder: you@company.com
        - generic [ref=e18]:
          - generic [ref=e19]: Password
          - generic [ref=e20]:
            - textbox "Password" [ref=e21]:
              - /placeholder: ••••••••
            - button "Show password" [ref=e22] [cursor=pointer]:
              - img [ref=e23]
        - button "Sign In" [ref=e27] [cursor=pointer]
      - generic [ref=e30]: or
      - button "Try Live Demo" [ref=e32] [cursor=pointer]:
        - img [ref=e33]
        - text: Try Live Demo
      - generic [ref=e35]:
        - img [ref=e36]
        - paragraph [ref=e39]: Encrypted · Authorized Personnel Only
    - generic [ref=e40]:
      - paragraph [ref=e41]: "Admin: admin@acp.local / admin1234"
      - paragraph [ref=e42]: "Demo: demo@aegisagent.in / demo1234"
  - alertdialog "Unauthorized Access" [ref=e43]:
    - generic [ref=e45]:
      - generic [ref=e48]:
        - img [ref=e50]
        - generic [ref=e52]:
          - generic [ref=e54]: Security Incident
          - heading "Unauthorized Access" [level=2] [ref=e56]
          - paragraph [ref=e57]: Authentication boundary violated — session terminated
      - generic [ref=e58]:
        - generic [ref=e59]:
          - generic [ref=e60]:
            - paragraph [ref=e61]: Incident ID
            - paragraph [ref=e62]: c84b6f86-16fb-4202…
          - generic [ref=e63]:
            - paragraph [ref=e64]: Timestamp
            - paragraph [ref=e65]: 11:57:13 AM
          - generic [ref=e66]:
            - paragraph [ref=e67]: Reason Code
            - paragraph [ref=e68]: unauthorized
          - generic [ref=e69]:
            - paragraph [ref=e70]: Request Path
            - paragraph [ref=e71]: /audit/fleet/timeseries?metric=decisions&window_minutes=180&bucket_minutes=5
        - generic [ref=e72]:
          - img [ref=e73]
          - paragraph [ref=e75]: This event has been recorded in the audit log. If you believe this is unauthorized access, contact your security team with the incident ID above.
        - generic [ref=e76]:
          - img [ref=e77]
          - generic [ref=e80]:
            - text: Redirecting to login in
            - generic [ref=e81]: 12s
      - generic [ref=e82]:
        - button "Re-authenticate now" [ref=e83] [cursor=pointer]:
          - img [ref=e84]
          - text: Re-authenticate
        - button "Copy incident report" [ref=e87] [cursor=pointer]:
          - img [ref=e88]
          - text: Copy Report
```

# Test source

```ts
  1   | // fleet.spec.ts — Sprint 4
  2   | // Behavioural e2e for the Fleet Home dashboard.
  3   | //
  4   | // Pins:
  5   | //   * page renders without pageerror / console errors
  6   | //   * GET /audit/fleet/kpis is hit and returns 200
  7   | //   * GET /audit/fleet/timeseries is hit and returns 200
  8   | //   * KPI card labels render (Decisions / Deny rate / Error rate / Active agents)
  9   | //   * metric toggle (Decisions → Denied) issues a fresh /timeseries call
  10  | //   * window selector (Last 1h → Last 24h) re-fetches both KPIs and series
  11  | //
  12  | // Run:
  13  | //   PLAYWRIGHT_USER=admin@aegisagent.in \
  14  | //     PLAYWRIGHT_PASSWORD=... \
  15  | //     AEGIS_BASE_URL=http://localhost:5173 \
  16  | //     npm run test:e2e -- fleet.spec.ts
  17  | 
  18  | import { test, expect } from '@playwright/test'
  19  | import { loginViaApi } from './_helpers/login'
  20  | 
  21  | const PASSWORD = process.env.PLAYWRIGHT_PASSWORD || ''
  22  | 
  23  | test.describe('fleet dashboard', () => {
  24  |   test.skip(!PASSWORD, 'PLAYWRIGHT_PASSWORD not set — skipping live fleet test')
  25  | 
  26  |   test('renders KPI cards and time-series; metric + window toggles re-fetch', async ({ page }) => {
  27  |     // Boot-time invariants the page must not violate.
  28  |     const errors: string[] = []
  29  |     page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  30  |     page.on('console', (m) => {
  31  |       if (m.type() === 'error') errors.push(`console: ${m.text()}`)
  32  |     })
  33  | 
  34  |     // Watch the two backend endpoints the Fleet page consumes.
  35  |     const kpiCalls: string[] = []
  36  |     const tsCalls: string[] = []
  37  |     page.on('response', (resp) => {
  38  |       const url = resp.url()
  39  |       if (url.includes('/audit/fleet/kpis')) {
  40  |         kpiCalls.push(url)
  41  |         expect(resp.status(), `kpis non-200: ${url}`).toBe(200)
  42  |       }
  43  |       if (url.includes('/audit/fleet/timeseries')) {
  44  |         tsCalls.push(url)
  45  |         expect(resp.status(), `timeseries non-200: ${url}`).toBe(200)
  46  |       }
  47  |     })
  48  | 
  49  |     await loginViaApi(page)
  50  |     await page.goto('/fleet')
  51  | 
  52  |     // Heading must surface.
  53  |     await expect(page.getByRole('heading', { name: /^Fleet$/i }))
  54  |       .toBeVisible({ timeout: 10_000 })
  55  | 
  56  |     // KPI card labels — the dashboard's headline contract. The card
  57  |     // labels live in <span> elements; "Decisions" doubles as a metric-
  58  |     // toggle <button> below the cards, so we scope the locator to span
  59  |     // to disambiguate.
  60  |     for (const label of ['Decisions', 'Deny rate', 'Error rate', 'Active agents']) {
  61  |       await expect(
  62  |         page.locator('span').filter({ hasText: new RegExp(`^${label}$`) }),
  63  |       ).toBeVisible({ timeout: 10_000 })
  64  |     }
  65  | 
  66  |     // Wait for the initial fetch wave to land.
> 67  |     await page.waitForLoadState('networkidle')
      |                ^ Error: page.waitForLoadState: Test ended.
  68  |     expect(kpiCalls.length, 'no /audit/fleet/kpis observed').toBeGreaterThan(0)
  69  |     expect(tsCalls.length, 'no /audit/fleet/timeseries observed').toBeGreaterThan(0)
  70  |     const kpisBefore = kpiCalls.length
  71  |     const tsBefore = tsCalls.length
  72  | 
  73  |     // Metric toggle: click "Denied" — must fire a fresh /timeseries call
  74  |     // (KPI cards don't re-query because they're metric-agnostic).
  75  |     await page.getByRole('button', { name: 'Denied' }).click()
  76  |     await page.waitForLoadState('networkidle')
  77  |     expect(
  78  |       tsCalls.length,
  79  |       `metric toggle did not refetch /audit/fleet/timeseries (before=${tsBefore}, after=${tsCalls.length})`,
  80  |     ).toBeGreaterThan(tsBefore)
  81  |     // The new URL must carry metric=denied.
  82  |     expect(tsCalls.some((u) => u.includes('metric=denied')), `no metric=denied call: ${tsCalls.join('\n')}`).toBeTruthy()
  83  | 
  84  |     // Window selector: change to "Last 24h" — re-fetches BOTH endpoints.
  85  |     await page.locator('select').first().selectOption({ label: 'Last 24h' })
  86  |     await page.waitForLoadState('networkidle')
  87  |     expect(
  88  |       kpiCalls.length,
  89  |       `window selector did not refetch /audit/fleet/kpis (before=${kpisBefore}, after=${kpiCalls.length})`,
  90  |     ).toBeGreaterThan(kpisBefore)
  91  |     expect(
  92  |       kpiCalls.some((u) => u.includes('window_minutes=1440')),
  93  |       'no window_minutes=1440 in any KPI call',
  94  |     ).toBeTruthy()
  95  | 
  96  |     // Cross-page link: "Open Agent Health" must navigate.
  97  |     await page.getByRole('link', { name: /Open Agent Health/i }).click()
  98  |     await expect(page.getByRole('heading', { name: /^Agent Health$/i }))
  99  |       .toBeVisible({ timeout: 10_000 })
  100 | 
  101 |     expect(errors, `unexpected console / page errors:\n${errors.join('\n')}`).toHaveLength(0)
  102 |   })
  103 | })
  104 | 
```