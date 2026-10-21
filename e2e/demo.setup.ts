import { expect, test as setup } from '@playwright/test'

setup('open a demo workspace from the landing page', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Open a live demo workspace' }).click()
  await expect(page).toHaveURL(/\/app$/)
  await expect(page.getByText('web · EMEA: volume drop on 2 days')).toBeVisible()
  await page.context().storageState({ path: '.auth/demo.json' })
})
