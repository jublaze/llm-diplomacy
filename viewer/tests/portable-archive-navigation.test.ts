// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildReportCatalog } from '../scripts/build_report_catalog.ts';
import { syntheticReport } from './synthetic-report.ts';
import { build as viteBuild } from 'vite';

const viewerRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const hash = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
function files(root: string): string[] {
  const result: string[] = [];
  const visit = (directory: string) => {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, item.name);
      if (item.isDirectory()) visit(path);
      else if (item.isFile()) result.push(relative(root, path).replaceAll('\\', '/'));
    }
  };
  visit(root);
  return result.sort();
}

describe('portable archive navigation', () => {
  it('opens the default evidence phase for short, post-1910, and legacy-duration archives', async () => {
    const root = mkdtempSync(join(tmpdir(), 'vci-archive-nav-'));
    try {
      const sourceRoot = join(root, 'source');
      const publicRoot = join(root, 'public');
      const packageRoot = join(root, 'package');
      const archive = join(root, 'reports.zip');
      for (const [id, end] of [['game_001', 1901], ['game_002', 1912], ['game_003', 1910]] as const) {
        const reportRoot = join(sourceRoot, id);
        mkdirSync(reportRoot, { recursive: true });
        const report = syntheticReport(1901, end) as any;
        report.source.game_id = id;
        report.bundle_id = `archive-${id}`;
        writeFileSync(join(reportRoot, 'report.json'), JSON.stringify(report));
      }
      buildReportCatalog({ sourceRoot, outputRoot: join(publicRoot, 'reports') });
      cpSync(join(viewerRoot, 'public', 'maps'), join(publicRoot, 'maps'), { recursive: true });
      mkdirSync(join(packageRoot, 'site'), { recursive: true });
      await viteBuild({ root: viewerRoot, base: './', publicDir: publicRoot, logLevel: 'silent', build: { outDir: join(packageRoot, 'site'), emptyOutDir: true } });
      const packageFiles = files(packageRoot).map((path) => { const bytes = readFileSync(join(packageRoot, ...path.split('/'))); return { path, bytes: bytes.length, sha256: hash(bytes) }; });
      writeFileSync(join(packageRoot, 'package-manifest.json'), JSON.stringify({ schema_version: 'diplomacy_archive_manifest_v1', package_id: 'synthetic-navigation-regression', audience: 'omniscient_postgame', report_ids: ['game_001', 'game_002', 'game_003'], catalog_sha256: hash(readFileSync(join(publicRoot, 'reports', 'catalog.json'))), files: packageFiles }, null, 2));
      const created = spawnSync('python', ['scripts/archive_report.py', 'create', '--package-root', packageRoot, '--output', archive], { cwd: viewerRoot, encoding: 'utf8' });
      expect(created.status, `${created.stdout}\n${created.stderr}`).toBe(0);
      const browser = spawnSync(process.execPath, ['--experimental-strip-types', 'scripts/verify_report_archive.ts', '--archive', archive, '--screenshot-root', join(root, 'screenshots')], { cwd: viewerRoot, encoding: 'utf8', timeout: 120_000 });
      expect(browser.status, `${browser.error ?? ''}\n${browser.stdout ?? ''}\n${browser.stderr ?? ''}`).toBe(0);
      const result = JSON.parse(browser.stdout.trim().split(/\r?\n/).at(-1)!);
      expect(result.reports).toEqual(['game_001', 'game_002', 'game_003']);
      expect(result.escaped_requests).toBe(0);
      expect(result.failed_requests).toBe(0);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }, 120_000);
});
