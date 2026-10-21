import { expect, test as setup } from '@playwright/test'

setup('open a workspace from the landing page', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Open your workspace' }).click()
  await expect(page).toHaveURL(/\/app$/)
  await expect(page.getByText('web · EMEA: volume drop on 2 days')).toBeVisible()
  await page.context().storageState({ path: '.auth/demo.json' })
})
