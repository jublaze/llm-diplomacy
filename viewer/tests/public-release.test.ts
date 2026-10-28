// @vitest-environment node
import { appendFileSync, existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import sourceReport from '../fixtures/game_001/report.json';
import { buildPublicRelease, verifyPublicRelease } from '../scripts/build_public_release';
import { startPublicReleaseServer } from '../scripts/serve_public_release';

describe('Phase 5D-local public release gate', () => {
  it('builds a deterministic public-only release, serves it on loopback, and detects tampering', async () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-phase-5d-local-'));
    const first = join(root, 'first');
    const second = join(root, 'second');
    const options = { catalogPath: resolve('fixtures/reproducibility-v1.catalog.json'), reportIds: ['game_001'] };

    const firstManifest = await buildPublicRelease({ ...options, outputRoot: first });
    const secondManifest = await buildPublicRelease({ ...options, outputRoot: second });
    expect(firstManifest).toEqual(secondManifest);
    expect(readFileSync(join(first, 'deploy-manifest.json'))).toEqual(readFileSync(join(second, 'deploy-manifest.json')));
    expect(readFileSync(join(first, 'site.zip'))).toEqual(readFileSync(join(second, 'site.zip')));
    expect(firstManifest).toMatchObject({
      schema_version: 'diplomacy_public_release_manifest_v1',
      audience: 'public',
      hosting_status: 'local_only',
      publication_approved: false,
      base_path: './',
      report_ids: ['game_001'],
    });
    expect(verifyPublicRelease(first)).toEqual([]);

    const catalog = JSON.parse(readFileSync(join(first, 'site-package', 'site', 'reports', 'catalog.json'), 'utf8'));
    expect(catalog.reports).toHaveLength(1);
    expect(catalog.reports[0]).toMatchObject({ id: 'game_001', audience: 'public' });
    const publicReportPath = join(first, 'site-package', 'site', 'reports', ...catalog.reports[0].report_url.split('/'));
    expect(existsSync(publicReportPath)).toBe(true);
    const publicReportText = readFileSync(publicReportPath, 'utf8');
    const publicReport = JSON.parse(publicReportText);
    expect(publicReport.private_records).toEqual({});
    expect(Object.values(publicReport.messages).every((message: any) => message.visibility === 'public')).toBe(true);
    expect(Object.values(publicReport.evidence_index).every((source: any) => source.visibility === 'public')).toBe(true);
    const excludedPrivateText = Object.values(sourceReport.messages).find((message: any) => message.visibility === 'private')!.text;
    expect(publicReportText).not.toContain(excludedPrivateText);
    expect(readFileSync(join(first, 'site-package', 'site', 'index.html'), 'utf8')).toMatch(/(?:src|href)="\.\/assets\//);

    const preview = await startPublicReleaseServer(first, 0);
    try {
      expect(preview.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/$/);
      const response = await fetch(preview.url);
      expect(response.status).toBe(200);
      expect(await response.text()).toMatch(/<div id="root"><\/div>/);
    } finally {
      await preview.close();
    }

    appendFileSync(join(first, 'site-package', 'README.md'), '\ntampered\n');
    expect(verifyPublicRelease(first).join('\n')).toMatch(/checksum|size/i);
  }, 90_000);
});
