import { expect, test } from '@playwright/test'

test('landing page shows measured model results from the API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Bad data doesn’t announce itself/ })).toBeVisible()
  await expect(page.getByText('Records · average precision')).toBeVisible()
  await expect(page.getByText(/false flags on [\d,]+ clean rows/)).toBeVisible()
})

test.describe('in the demo workspace', () => {
test.use({ storageState: '.auth/demo.json' })

test('incidents open with evidence and an analyst can triage them', async ({ page }) => {
  await page.goto('/app')
  await expect(page.getByText('web · EMEA: volume drop on 2 days')).toBeVisible()

  await page.getByRole('link', { name: /^Incidents/ }).click()
  await page.getByRole('link', { name: /unusual `unit_price` values/ }).click()
  await expect(page.getByText(/Baseline evidence: [\d,]+ rows with/).first()).toBeVisible()
  await page.getByRole('button', { name: 'Start investigating' }).click()
  await expect(page.getByText('Status open → investigating')).toBeVisible()
  await page.getByLabel('Comment').fill('E2E: checked with the catalog team.')
  await page.getByRole('button', { name: 'Comment', exact: true }).click()
  await expect(page.getByText('E2E: checked with the catalog team.')).toBeVisible()
})

test('fault lab: injected faults are scored against ground truth', async ({ page }) => {
  await page.goto('/app/lab')
  await page.getByRole('button', { name: 'Replayed load' }).click()
  await page.getByRole('button', { name: /Inject 1 fault/ }).click()
  await expect(page.getByRole('cell', { name: 'duplicates' })).toBeVisible()
  const row = page.getByRole('row', { name: /duplicates/ })
  await expect(row.getByText('100%')).toBeVisible()
})
})

test('register, upload a CSV, see the baseline analysed', async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`
  await page.goto('/register')
  await page.getByLabel('Your name').fill('E2E Tester')
  await page.getByLabel('Organization').fill('E2E Corp')
  await page.getByLabel('Work email').fill(email)
  await page.getByLabel('Password').fill('e2e-password-123')
  await page.getByRole('button', { name: 'Create workspace' }).click()
  await expect(page.getByRole('heading', { name: 'Watchtower' })).toBeVisible()

  const header = 'order_id,created_at,store,amount\n'
  const rows = Array.from({ length: 400 }, (_, i) =>
    `A${i},2026-03-${String(1 + (i % 28)).padStart(2, '0')}T10:00:00,S${i % 3},${(20 + (i % 13) * 1.5).toFixed(2)}`)
  await page.goto('/app/datasets')
  await page.getByRole('button', { name: 'New dataset' }).click()
  await page.getByLabel('Name').fill('E2E sales')
  await page.locator('input[type=file]').setInputFiles({ name: 'sales.csv', mimeType: 'text/csv', buffer: Buffer.from(header + rows.join('\n')) })
  await page.getByRole('button', { name: 'Upload & analyse' }).click()
  await expect(page.getByRole('heading', { name: 'E2E sales' })).toBeVisible()
  await expect(page.getByText('Baseline version.')).toBeVisible()
  await expect(page.getByText('400', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('e2e-password-123')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Watchtower' })).toBeVisible()
})
