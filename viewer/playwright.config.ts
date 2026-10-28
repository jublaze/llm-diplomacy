import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e', outputDir: './test-results/playwright', timeout: 30_000, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:4173', trace: 'retain-on-failure' },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'], browserName: 'chromium' } }, { name: 'narrow', use: { browserName: 'chromium', viewport: { width: 390, height: 844 } } }],
  // Reuse a loopback server when the Windows automation harness owns one; this
  // avoids waiting on Playwright's child-process teardown after assertions pass.
  webServer: { command: 'node ./node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173', url: 'http://127.0.0.1:4173', reuseExistingServer: true }
});
