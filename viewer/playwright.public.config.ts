import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/public-e2e',
  outputDir: './test-results/public-playwright',
  timeout: 30_000,
  fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:4175', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], browserName: 'chromium' } },
    { name: 'narrow', use: { browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
});
