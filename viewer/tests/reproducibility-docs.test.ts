// @vitest-environment node
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { verifyPackageDirectory } from '../scripts/package_report';
import { validateCatalog } from '../src/report-loader';
import { validateSchema } from '../src/report-model/validate-schema';
import { validateBundle } from '../src/report-model/validate';

const root = resolve('.');
const documentation = [
  'docs/reproducibility/v1/README.md',
  'docs/reproducibility/v1/REPORT_CONTRACT.md',
  'docs/reproducibility/v1/REPRODUCTION.md',
  'docs/reproducibility/v1/ATTRIBUTION_AND_PROVENANCE.md',
  'docs/reproducibility/v1/EVIDENCE_AUDIT_CHECKLIST.md',
];
const sampleCatalogPath = 'fixtures/reproducibility-v1.catalog.json';
const digest = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');

describe('Phase 5C reproducibility materials', () => {
  it('publishes a complete versioned documentation set with valid local links and a usable sample catalog', () => {
    for (const relativePath of documentation) expect(existsSync(join(root, relativePath)), relativePath).toBe(true);
    const combined = documentation.map((relativePath) => readFileSync(join(root, relativePath), 'utf8')).join('\n');
    for (const required of [
      /diplomacy_report_bundle_v1/i, /input layout/i, /export command/i, /audience policy/i,
      /historian_narrative_v1/i, /source kinds/i, /map attribution/i, /model.*prompt provenance/i,
      /known limitations/i, /integrity verification/i,
    ]) expect(combined).toMatch(required);
    expect(combined).not.toMatch(/[A-Za-z]:\\Users\\/);

    for (const relativePath of documentation) {
      const absolutePath = join(root, relativePath);
      const markdown = readFileSync(absolutePath, 'utf8');
      for (const match of markdown.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
        const target = match[1].split('#')[0];
        if (!target || /^(?:https?:|mailto:)/.test(target)) continue;
        expect(existsSync(resolve(dirname(absolutePath), target)), `${relativePath} -> ${target}`).toBe(true);
      }
    }

    const catalog = validateCatalog(JSON.parse(readFileSync(join(root, sampleCatalogPath), 'utf8')));
    expect(catalog.reports).toHaveLength(1);
    expect(catalog.reports[0]).toMatchObject({ id: 'game_001', fixture: true, audience: 'omniscient_postgame' });
    const reportPath = resolve(dirname(join(root, sampleCatalogPath)), catalog.reports[0].report_url);
    const reportBytes = readFileSync(reportPath);
    expect(digest(reportBytes)).toBe(catalog.reports[0].sha256);
    const report = JSON.parse(reportBytes.toString('utf8'));
    expect([...validateSchema(report), ...validateBundle(report)]).toEqual([]);
  });

  it('runs the documented fixture-only package reproduction in a clean temporary destination', async () => {
    const destination = join(mkdtempSync(join(tmpdir(), 'viewer-phase-5c-')), 'sample-package');
    const reproduction = spawnSync(process.execPath, [
      '--experimental-strip-types', 'scripts/package_report.ts',
      '--catalog', sampleCatalogPath, '--audience', 'omniscient_postgame',
      '--report-ids', 'game_001', '--output-root', destination,
    ], { cwd: root, encoding: 'utf8' });
    expect(reproduction.status, reproduction.stderr || reproduction.stdout).toBe(0);
    expect(verifyPackageDirectory(destination)).toEqual([]);
    for (const file of ['README.md', 'ATTRIBUTION.md', 'LICENSE-INVENTORY.md', 'EVIDENCE-AUDIT-CHECKLIST.md', 'package-manifest.json']) {
      expect(existsSync(join(destination, file)), file).toBe(true);
    }
    expect(readFileSync(join(destination, 'README.md'), 'utf8')).toMatch(/integrity.*npm run verify:archive/is);
    expect(readFileSync(join(destination, 'ATTRIBUTION.md'), 'utf8')).toMatch(/standard_v1.*standard_geometry_v1/is);
    expect(readFileSync(join(destination, 'LICENSE-INVENTORY.md'), 'utf8')).toMatch(/license status/i);
    expect(readFileSync(join(destination, 'EVIDENCE-AUDIT-CHECKLIST.md'), 'utf8')).toMatch(/<completed-experiment-root>/);
  }, 60_000);
});
