// Playwright config — sprint-3.2.
// Replaces the prior `tests/test_phase*_ui.py` string-match contract tests
// with real browser-driven behavioural tests. Phase tests broke on every
// cosmetic rename and caught zero behavioural bugs.
//
// Run locally:
//   cd ui
//   npm install
//   npm run test:e2e:install     # one-time chromium download
//   AEGIS_BASE_URL=http://localhost:5173 npm run test:e2e
//
// Run in CI: see .github/workflows/test.yml (e2e job runs against a
// docker-compose stack spun up by the workflow itself).

import { defineConfig, devices } from '@playwright/test'

const BASE_URL = process.env.AEGIS_BASE_URL || 'http://localhost:5173'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',

  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: process.env.CI ? 'retain-on-failure' : 'off',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
