// @vitest-environment node
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { packageReport, verifyPackageDirectory } from '../scripts/package_report';
import { buildAudienceProjections } from '../scripts/build_audience_projections';

describe('Phase 4D static package builder', () => {
  it('builds a deterministic relative-base package containing only selected catalog files', async () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-package-'));
    const firstRoot = join(root, 'first');
    const secondRoot = join(root, 'second');
    const options = { catalogPath: 'fixtures/reproducibility-v1.catalog.json', reportIds: ['game_001'], audience: 'omniscient_postgame' as const };

    const first = await packageReport({ ...options, outputRoot: firstRoot });
    const second = await packageReport({ ...options, outputRoot: secondRoot });

    expect(first.manifest).toEqual(second.manifest);
    expect(readFileSync(join(firstRoot, 'package-manifest.json'))).toEqual(readFileSync(join(secondRoot, 'package-manifest.json')));
    const packagedCatalog = JSON.parse(readFileSync(join(firstRoot, 'site', 'reports', 'catalog.json'), 'utf8'));
    expect(packagedCatalog.reports.map((entry: any) => entry.id)).toEqual(['game_001']);
    expect(existsSync(join(firstRoot, 'site', 'reports', packagedCatalog.reports[0].report_url))).toBe(true);
    expect(packagedCatalog.reports.some((entry: any) => entry.id === 'game_002')).toBe(false);
    expect(readFileSync(join(firstRoot, 'site', 'index.html'), 'utf8')).toMatch(/(?:src|href)="\.\/assets\//);
    expect(verifyPackageDirectory(firstRoot)).toEqual([]);
  }, 60_000);

  it('refuses to relabel an omniscient catalog as a public package', async () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-public-package-'));
    await expect(packageReport({ catalogPath: 'fixtures/reproducibility-v1.catalog.json', outputRoot: join(root, 'public'), audience: 'public' })).rejects.toThrow(/public.*omniscient|audience/i);
    expect(existsSync(join(root, 'public'))).toBe(false);
  });

  it('packages already-projected public and player catalogs as separate artifacts', async () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-audience-packages-'));
    const projections = join(root, 'projections');
    buildAudienceProjections({ catalogPath: 'fixtures/reproducibility-v1.catalog.json', outputRoot: projections, audiences: ['public', 'player-birsan'] });
    const publicRoot = join(root, 'public-package');
    const playerRoot = join(root, 'player-package');
    await packageReport({ catalogPath: join(projections, 'public', 'catalog.json'), outputRoot: publicRoot, audience: 'public', reportIds: ['game_001'] });
    await packageReport({ catalogPath: join(projections, 'player-birsan', 'catalog.json'), outputRoot: playerRoot, audience: 'player-birsan', reportIds: ['game_001'] });

    const publicReport = JSON.parse(readFileSync(join(publicRoot, 'site', 'reports', 'bundles', 'game_001', 'report.json'), 'utf8'));
    const playerReport = JSON.parse(readFileSync(join(playerRoot, 'site', 'reports', 'bundles', 'game_001', 'report.json'), 'utf8'));
    expect(publicReport.private_records).toEqual({});
    expect(Object.values(publicReport.messages).every((message: any) => message.visibility === 'public')).toBe(true);
    expect(Object.values(playerReport.private_records).every((record: any) => record.agent_id === 'birsan')).toBe(true);
    expect(readFileSync(join(playerRoot, 'README.md'), 'utf8')).toMatch(/separate data artifact, not authentication/i);
    expect(verifyPackageDirectory(publicRoot)).toEqual([]);
    expect(verifyPackageDirectory(playerRoot)).toEqual([]);
  }, 60_000);
});
