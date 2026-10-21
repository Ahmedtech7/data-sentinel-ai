import { defineConfig } from '@playwright/test'

// BASE_URL=https://your-deployment npx playwright test   (defaults to a local server)
export default defineConfig({
  testDir: '.',
  timeout: 180_000, // free-tier hosts cold-start in ~1 min
  expect: { timeout: 90_000 },
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: process.env.BASE_URL ?? 'http://localhost:8765', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    // One workspace per run: the endpoint is rate-limited per IP.
    { name: 'demo-setup', testMatch: /demo\.setup\.ts/ },
    { name: 'e2e', testMatch: /\.spec\.ts/, dependencies: ['demo-setup'] },
  ],
})
