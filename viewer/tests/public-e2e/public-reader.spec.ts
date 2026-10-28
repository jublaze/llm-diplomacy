import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('public artifact exposes only the postgame-release perspective', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /read the war through its evidence/i })).toBeVisible();
  await expect(page.getByText(/postgame public release/i)).toHaveCount(2);
  await expect(page.getByText(/omniscient research/i)).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: `docs/phase-5b-deployment-screenshots/public-library-${testInfo.project.name}.png`, fullPage: true });
});

test('public deep links retain map, orders, search, and released threads without private affordances', async ({ page }, testInfo) => {
  await page.goto('/#game=game_001&phase=1902_spring_movement');
  await expect(page.getByRole('heading', { name: 'Spring 1902 scene' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Public orders and adjudicated board' })).toBeVisible();
  await expect(page.locator('.perspective-bar b')).toHaveText('Postgame public release.');
  await expect(page.getByRole('button', { name: /private records/i })).toHaveCount(0);
  await page.getByRole('button', { name: /orders & adjudication/i }).click();
  await expect(page.getByRole('dialog', { name: 'Evidence drawer' })).toBeVisible();
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByLabel(/search power, agent, province/i).fill('persistent_memory');
  await expect(page.getByText(/0 results/i)).toBeVisible();
  await expect(page.locator('.result-kind', { hasText: 'record' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Threads', exact: true }).click();
  await expect(page.getByText('Postgame-released negotiation transcript', { exact: true })).toBeVisible();
  await expect(page.getByText(/not public facts during play/i)).toBeVisible();
  await page.locator('.thread-list details').first().locator('summary').click();
  await page.getByRole('button', { name: 'Open message evidence' }).first().click();
  const drawer = page.getByRole('dialog', { name: 'Evidence drawer' });
  await expect(drawer).toContainText('Postgame-released negotiation transcript');
  await expect(drawer).toContainText('released/negotiation_transcript.json');
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: `docs/phase-5b-deployment-screenshots/public-thread-${testInfo.project.name}.png`, fullPage: true });
});

test('a second-game public deep link resolves using only packaged assets', async ({ page }) => {
  await page.goto('/#game=game_002&phase=1906_fall_movement');
  await expect(page.getByRole('heading', { name: 'Fall 1906 scene' })).toBeVisible();
  await expect(page).toHaveURL(/game=game_002.*phase=1906_fall_movement/);
  await expect(page.locator('.analysis-map').getByRole('group', { name: /after adjudication map/i })).toBeVisible();
});
