// @vitest-environment node
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildAudienceProjections } from '../scripts/build_audience_projections';

describe('Phase 5A audience projection builder', () => {
  it('writes byte-identical isolated catalogs and reports across two builds', () => {
    const root = mkdtempSync(join(tmpdir(), 'audience-builder-'));
    const options = { catalogPath: 'fixtures/reproducibility-v1.catalog.json', audiences: ['public', 'player-birsan', 'omniscient_postgame'] as const };
    const first = join(root, 'first');
    const second = join(root, 'second');
    const manifest = buildAudienceProjections({ ...options, audiences: [...options.audiences], outputRoot: first });
    buildAudienceProjections({ ...options, audiences: [...options.audiences], outputRoot: second });

    expect(manifest.audiences.map((item) => item.audience)).toEqual(['public', 'player-birsan', 'omniscient_postgame']);
    for (const relative of ['projection-manifest.json', 'public/catalog.json', 'public/bundles/game_001/report.json', 'player-birsan/bundles/game_001/report.json', 'omniscient_postgame/bundles/game_001/report.json']) {
      expect(readFileSync(join(first, relative))).toEqual(readFileSync(join(second, relative)));
    }
    const publicText = readFileSync(join(first, 'public', 'bundles', 'game_001', 'report.json'), 'utf8');
    const publicReport = JSON.parse(publicText);
    expect(publicReport.source.visibility_mode).toBe('public');
    expect(publicReport.private_records).toEqual({});
    expect(publicText).not.toContain('omniscient_postgame');
  }, 60_000);
});
