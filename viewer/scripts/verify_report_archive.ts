import { createServer } from 'node:http';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { extname, join, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { chromium } from 'playwright';
import { archiveNavigationTarget } from '../src/archive-navigation.ts';

const option = (name: string) => {
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
};
const archivePath = option('--archive');
const screenshotRoot = option('--screenshot-root');
const mime: Record<string, string> = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png' };

async function main() {
  if (!archivePath || !screenshotRoot) throw new Error('Usage: node --experimental-strip-types scripts/verify_report_archive.ts --archive <archive.zip> --screenshot-root <directory>');
  mkdirSync(resolve(screenshotRoot), { recursive: true });
  const taskRoot = mkdtempSync(join(tmpdir(), 'diplomacy-archive-browser-'));
  const extracted = join(taskRoot, 'extracted');
  try {
    const verified = spawnSync('python', ['scripts/archive_report.py', 'verify', '--archive', archivePath, '--extract-to', extracted], { cwd: process.cwd(), encoding: 'utf8' });
    if (verified.status !== 0) throw new Error(`Archive extraction verification failed: ${verified.stderr || verified.stdout}`);
    const site = resolve(extracted, 'site');
    const prefix = '/portable/';
    const escapedRequests: string[] = [];
    const failedRequests: string[] = [];
    const server = createServer((request, response) => {
      try {
        const pathname = decodeURIComponent(new URL(request.url ?? '/', 'http://127.0.0.1').pathname);
        if (!pathname.startsWith(prefix)) { escapedRequests.push(pathname); response.writeHead(404).end('outside package mount'); return; }
        const relativePath = pathname.slice(prefix.length) || 'index.html';
        let path = resolve(site, ...relativePath.split('/'));
        if (path !== site && !path.startsWith(`${site}${sep}`)) { escapedRequests.push(pathname); response.writeHead(403).end('path escape'); return; }
        if (existsSync(path) && statSync(path).isDirectory()) path = join(path, 'index.html');
        if (!existsSync(path) || !statSync(path).isFile()) { failedRequests.push(pathname); response.writeHead(404).end('not found'); return; }
        response.writeHead(200, { 'content-type': mime[extname(path)] ?? 'application/octet-stream', 'cache-control': 'no-store' });
        response.end(readFileSync(path));
      } catch (error) { response.writeHead(500).end(error instanceof Error ? error.message : String(error)); }
    });
    await new Promise<void>((accept) => server.listen(0, '127.0.0.1', accept));
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Static verification server did not expose a TCP port.');
    const baseUrl = `http://127.0.0.1:${address.port}${prefix}`;
    const catalog = JSON.parse(readFileSync(join(site, 'reports', 'catalog.json'), 'utf8'));
    const browser = await chromium.launch();
    try {
      for (const viewport of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'narrow', width: 390, height: 844 }]) {
        const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
        const externalRequests: string[] = [];
        page.on('request', (request) => { if (!request.url().startsWith(`http://127.0.0.1:${address.port}${prefix}`)) externalRequests.push(request.url()); });
        await page.goto(baseUrl, { waitUntil: 'networkidle' });
        await page.getByRole('heading', { name: /read the war through its evidence/i }).waitFor();
        await page.screenshot({ path: join(screenshotRoot, `archive-library-${viewport.name}.png`), fullPage: true });
        if (catalog.reports.length > 1) {
          await page.getByRole('button', { name: /compare selected games/i }).click();
          await page.getByRole('table', { name: /recorded campaign metrics/i }).waitFor();
          await page.screenshot({ path: join(screenshotRoot, `archive-comparison-${viewport.name}.png`), fullPage: true });
          await page.getByRole('button', { name: /library/i }).click();
        }
        for (const entry of catalog.reports) {
          await page.getByRole('button', { name: `Open ${entry.label}` }).click();
          const reportPath = resolve(site, 'reports', ...entry.report_url.split('/'));
          if (!reportPath.startsWith(`${resolve(site, 'reports')}${sep}`)) throw new Error(`Unsafe report URL in archive catalog: ${entry.report_url}`);
          const report = JSON.parse(readFileSync(reportPath, 'utf8'));
          const target = archiveNavigationTarget(report);
          await page.getByRole('button', { name: target.chapterButton }).waitFor();
          await page.screenshot({ path: join(screenshotRoot, `archive-${entry.id}-${viewport.name}.png`), fullPage: true });
          await page.getByRole('button', { name: target.chapterButton }).click();
          await page.getByRole('heading', { name: target.heading }).waitFor();
          if (!page.url().includes(`phase=${encodeURIComponent(target.phaseId)}`)) throw new Error(`Archive navigation did not open ${target.phaseId}.`);
          await page.locator('.scene-tabs button.active').waitFor();
          await page.locator('.analysis-map').waitFor();
          await page.getByRole('button', { name: /library/i }).click();
        }
        if (externalRequests.length) throw new Error(`Browser requested resources outside the archive mount: ${externalRequests.join(', ')}`);
        await page.close();
      }
    } finally {
      await browser.close();
      await new Promise<void>((accept, reject) => server.close((error) => error ? reject(error) : accept()));
    }
    if (escapedRequests.length) throw new Error(`Requests escaped the archive mount: ${escapedRequests.join(', ')}`);
    if (failedRequests.length) throw new Error(`Archive requests failed: ${failedRequests.join(', ')}`);
    console.log(JSON.stringify({ archive: resolve(archivePath), reports: catalog.reports.map((entry: any) => entry.id), viewports: ['desktop', 'narrow'], escaped_requests: 0, failed_requests: 0 }));
  } finally {
    rmSync(taskRoot, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; });
