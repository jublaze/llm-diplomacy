import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('generated report home titles are reader-safe with closed research details', async ({ page }, testInfo) => {
  const catalog = await (await page.request.get('/reports/catalog.json')).json();
  const experimentId = catalog.reports.find((entry: any) => entry.id === 'game_001').experiment_id;
  const game2ExperimentId = catalog.reports.find((entry: any) => entry.id === 'game_002').experiment_id;
  await page.goto('/#game=game_001&view=home');
  await expect(page.getByRole('heading', { level: 1, name: 'Game 1: completed campaign' })).toBeVisible();
  const game1Hero = page.locator('.hero');
  await expect(game1Hero.getByRole('heading', { level: 1 })).not.toContainText(experimentId);
  await expect(game1Hero.getByText('1901–1910 campaign', { exact: true })).toBeVisible();
  const game1Research = game1Hero.locator('details', { hasText: 'Research details' });
  await expect(game1Research).not.toHaveAttribute('open', '');
  await expect(game1Research.getByText(experimentId, { exact: true })).not.toBeVisible();
  await game1Research.getByText('Research details', { exact: true }).click();
  await expect(game1Research).toContainText(experimentId);
  await game1Research.getByText('Research details', { exact: true }).click();
  await expect(page.getByRole('heading', { name: /evidence-led overview/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /every phase is available/i })).toBeVisible();
  await page.screenshot({ path: 'docs/phase-1-screenshots/game-1-home-' + testInfo.project.name + '.png', fullPage: true });
  await page.screenshot({ path: 'docs/phase-4b-screenshots/game-1-generated-' + testInfo.project.name + '.png', fullPage: true });
  await page.screenshot({ path: 'docs/title-presentation-screenshots/game-1-home-' + testInfo.project.name + '.png', fullPage: true });
  await page.getByRole('button', { name: /library/i }).click();
  await page.getByRole('button', { name: 'Open Game 2' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Game 2: completed campaign' })).toBeVisible();
  const game2Research = page.locator('.hero details', { hasText: 'Research details' });
  await expect(game2Research).not.toHaveAttribute('open', '');
  await expect(game2Research.getByText(game2ExperimentId, { exact: true })).not.toBeVisible();
  await game2Research.getByText('Research details', { exact: true }).click();
  await expect(game2Research).toContainText(game2ExperimentId);
  await game2Research.getByText('Research details', { exact: true }).click();
  await page.screenshot({ path: 'docs/title-presentation-screenshots/game-2-home-' + testInfo.project.name + '.png', fullPage: true });
  await expect(page.getByRole('heading', { name: /every phase is available/i })).toBeVisible();
});

test('generated catalog opens both games and preserves generic evidence reading', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /read the war through its evidence/i })).toBeVisible();
  await expect(page.getByText('Completed Game 1')).toBeVisible();
  await expect(page.getByText('Completed Game 2')).toBeVisible();
  await page.screenshot({ path: 'docs/phase-4b-screenshots/library-' + testInfo.project.name + '.png', fullPage: true });
  await page.getByRole('button', { name: 'Open Game 1' }).click();
  await expect(page).toHaveScreenshot('game-1-home-' + testInfo.project.name + '.png', { fullPage: true });
  await page.getByRole('button', { name: /read 1902 chapter/i }).click();
  await page.getByRole('button', { name: 'Fall 1902' }).click();
  await expect(page.getByRole('heading', { name: 'Fall 1902 scene' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /supporting evidence/i })).toBeVisible();
  await page.screenshot({ path: 'docs/phase-1-screenshots/game-1-1902-' + testInfo.project.name + '.png', fullPage: true });
  await expect(page).toHaveScreenshot('game-1-1902-' + testInfo.project.name + '.png', { fullPage: true });
  const cited = page.getByRole('button', { name: 'Open cited message' });
  await cited.click();
  await expect(page.getByRole('dialog', { name: 'Evidence drawer' })).toBeVisible();
  await expect(page.getByText('Correspondence context')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Evidence drawer' })).toHaveCount(0);
  await expect(cited).toBeFocused();
  await page.getByRole('button', { name: /library/i }).click();
  await page.getByRole('button', { name: 'Open Game 2' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Game 2: completed campaign' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /every phase is available/i })).toBeVisible();
});

test('deep-linked generated report supports later years, search, threads, dossiers, arcs, and evidence', async ({ page }, testInfo) => {
  await page.goto('/#game=game_002&phase=1906_fall_movement');
  await expect(page.getByRole('heading', { name: 'Fall 1906 scene' })).toBeVisible();
  await expect(page).toHaveURL(/game=game_002.*phase=1906_fall_movement/);
  await page.screenshot({ path: 'docs/phase-2-screenshots/game-2-1906-' + testInfo.project.name + '.png', fullPage: true });
  await page.screenshot({ path: 'docs/phase-4b-screenshots/game-2-deep-link-' + testInfo.project.name + '.png', fullPage: true });
  await expect(page).toHaveScreenshot('game-2-1906-' + testInfo.project.name + '.png', { fullPage: true, maxDiffPixels: 100 });

  await page.getByRole('button', { name: /library/i }).click();
  await page.getByRole('button', { name: 'Open Game 1' }).click();
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByLabel(/search power, agent, province/i).fill('transparent loop');
  await page.getByLabel('Search scope').selectOption('source');
  await expect(page.getByRole('button', { name: 'Open result' }).first()).toBeVisible();
  await page.screenshot({ path: 'docs/phase-2-screenshots/game-1-search-' + testInfo.project.name + '.png', fullPage: true });
  await expect(page).toHaveScreenshot('game-1-search-' + testInfo.project.name + '.png', { fullPage: true });
  await page.getByRole('button', { name: 'Open result' }).first().click();
  await expect(page.getByRole('dialog', { name: 'Evidence drawer' })).toBeVisible();
  await expect(page).toHaveURL(/view=search.*evidence=message%3A/);
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Threads', exact: true }).click();
  await page.getByLabel('Power').selectOption('italy');
  await page.getByLabel('Phase').selectOption('1902_spring_negotiation');
  await page.getByLabel('Channel').selectOption('private');
  await page.getByText('birsan ↔ helena').click();
  await page.getByRole('button', { name: 'Open message evidence' }).first().click();
  await expect(page.getByText('Source ID')).toBeVisible();
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Powers', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Power dossiers' })).toBeVisible();
  await page.getByRole('button', { name: 'Arcs', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Strategic arcs' })).toBeVisible();
});

test('an unavailable dynamically selected bundle fails closed', async ({ page }, testInfo) => {
  await page.route('**/reports/bundles/game_002/report.json', async (route) => {
    await route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"unavailable"}' });
  });
  await page.goto('/#game=game_002&view=home');
  await expect(page.getByRole('heading', { name: /unable to load selected report/i })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText(/HTTP 503/i);
  await expect(page.getByRole('button', { name: /return to library/i })).toBeVisible();
  await page.screenshot({ path: 'docs/phase-4b-screenshots/unavailable-report-' + testInfo.project.name + '.png', fullPage: true });
});

test('map analysis compares boards, filters public order marks, and opens exact adjudicated evidence', async ({ page }, testInfo) => {
  await page.goto('/#game=game_001&phase=1902_spring_movement');
  await expect(page.getByRole('heading', { name: 'Public orders and adjudicated board' })).toBeVisible();
  const map = page.locator('.analysis-map');
  await expect(map.getByRole('group', { name: /after adjudication map for spring 1902 movement with 34 selected public order marks/i })).toBeVisible();
  await page.screenshot({ path: 'docs/phase-3-screenshots/game-1-1902-movement-' + testInfo.project.name + '.png', fullPage: true });
  await expect(page).toHaveScreenshot('game-1-1902-movement-' + testInfo.project.name + '.png', { fullPage: true });

  await map.getByLabel('Before orders').check();
  await expect(map.getByRole('group', { name: /before orders map/i })).toBeVisible();
  await map.getByLabel('Emphasize power').selectOption('italy');
  await map.getByRole('checkbox', { name: 'Moves', exact: true }).uncheck();
  await expect(map.getByRole('group', { name: /18 selected public order marks/i })).toBeVisible();
  await map.getByRole('checkbox', { name: 'Moves', exact: true }).check();
  await map.getByLabel('Emphasize power').selectOption('');
  const vector = map.getByRole('button', { name: /^A bud–gal; move;/ });
  await vector.focus();
  await page.keyboard.press('Enter');
  const drawer = page.getByRole('dialog', { name: 'Evidence drawer' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('heading', { name: 'Public order', exact: true })).toBeVisible();
  await expect(page.getByText('Source ID')).toBeVisible();
  await page.screenshot({ path: 'docs/phase-3-screenshots/game-1-vector-evidence-' + testInfo.project.name + '.png', fullPage: true });
  await page.screenshot({ path: 'docs/phase-4b-screenshots/dynamic-vector-evidence-' + testInfo.project.name + '.png', fullPage: true });
  await page.keyboard.press('Escape');
  await expect(vector).toBeFocused();
  const failedVector = map.getByRole('button', { name: /^A ser–rum; move;/ });
  await failedVector.click();
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText('A ser–rum', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /library/i }).click();
  await expect(page.getByRole('heading', { name: /read the war through its evidence/i })).toBeVisible();
});

test('non-movement phases render retreats and adjustments', async ({ page }) => {
  await page.goto('/#game=game_001&phase=1902_spring_retreat');
  const map = page.locator('.analysis-map');
  await expect(page.getByLabel('Retreats')).toBeChecked();
  await expect(map.getByRole('group', { name: /after adjudication map.*1 selected public order marks/i })).toBeVisible();
  await page.getByRole('navigation', { name: 'Phase selector' }).getByRole('button', { name: 'Winter 1902', exact: true }).click();
  await expect(page.getByLabel('Builds')).toBeChecked();
  await expect(page.getByLabel('Disbands')).toBeChecked();
  await expect(map.getByRole('group', { name: /after adjudication map.*2 selected public order marks/i })).toBeVisible();
  await page.getByRole('button', { name: /library/i }).click();
  await expect(page.getByRole('heading', { name: /read the war through its evidence/i })).toBeVisible();
});

test('Game 2 uses the same generic standard-map renderer', async ({ page }) => {
  await page.goto('/#game=game_002&phase=1902_spring_movement');
  const genericMap = page.locator('.analysis-map');
  await expect(genericMap.getByRole('group', { name: /after adjudication map for spring 1902 movement/i })).toBeVisible();
  await page.getByRole('button', { name: /library/i }).click();
  await expect(page.getByRole('heading', { name: /read the war through its evidence/i })).toBeVisible();
});

test('reader has no automated accessibility violations', async ({ page }) => {
  await page.goto('/'); await page.getByRole('button', { name: 'Open Game 1' }).click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole('button', { name: /read 1902 chapter/i }).click();
  for (const season of ['Spring 1902', 'Fall 1902', 'Winter 1902']) {
    await page.getByRole('navigation', { name: 'Phase selector' }).getByRole('button', { name: season, exact: true }).click();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  }
  await page.getByRole('navigation', { name: 'Phase selector' }).getByRole('button', { name: 'Spring 1902', exact: true }).click();
  for (const button of [page.getByRole('button', { name: 'Open narrative source' }), page.getByRole('button', { name: 'Open cited message' }), page.getByRole('button', { name: /Private records \(/ }), page.getByRole('button', { name: /Orders & adjudication \(/ })]) {
    await button.click();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.getByRole('button', { name: 'Close evidence' }).click();
  }
});

test('generic explorer has no automated accessibility violations', async ({ page }) => {
  await page.goto('/#game=game_002&view=home');
  for (const view of ['Search', 'Threads', 'Powers', 'Arcs']) {
    await page.getByRole('button', { name: view, exact: true }).click();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  }
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByLabel(/search power, agent, province/i).fill('major');
  await page.getByLabel('Search scope').selectOption('agent');
  await page.getByRole('button', { name: 'Open result' }).first().click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test('map analysis has no automated accessibility violations', async ({ page }) => {
  await page.goto('/#game=game_001&phase=1902_spring_movement');
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByLabel('Before orders').check();
  await page.getByLabel('Emphasize power').selectOption('italy');
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole('navigation', { name: 'Phase selector' }).getByRole('button', { name: 'Winter 1902', exact: true }).click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test('cross-game comparison is descriptive, source-linked, accessible, and stable', async ({ page }, testInfo) => {
  await page.goto('/#view=comparison&games=game_001%2Cgame_002');
  await expect(page.getByRole('heading', { name: /compare recorded campaigns/i })).toBeVisible();
  await expect(page.getByRole('table', { name: /recorded campaign metrics/i })).toBeVisible();
  await expect(page.getByRole('table', { name: /supply-center changes by power/i })).toBeVisible();
  await expect(page.getByText(/no score, ranking, or causal claim/i)).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: 'docs/phase-5b-screenshots/comparison-' + testInfo.project.name + '.png', fullPage: true });
  await expect(page).toHaveScreenshot('comparison-' + testInfo.project.name + '.png', { fullPage: true });
  await page.getByRole('row', { name: /public messages/i }).getByRole('link').first().click();
  await expect(page).toHaveURL(/#game=game_001/);
});
