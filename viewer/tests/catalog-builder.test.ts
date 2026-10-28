import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { buildReportCatalog } from '../scripts/build_report_catalog';
import { createNarrativeOutline, createNarrativePlan, createTemplateNarrative } from '../src/narrative-generator';

describe('Phase 4B catalog builder', () => {
  it('builds a deterministic catalog and copied reports from arbitrary valid bundle paths', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-catalog-'));
    const source = join(root, 'source');
    const outputA = join(root, 'a');
    const outputB = join(root, 'b');
    writeFileSync(join(root, 'placeholder'), '');
    mkdirSync(join(source, 'nested', 'two'), { recursive: true });
    mkdirSync(join(source, 'nested', 'one'), { recursive: true });
    writeFileSync(join(source, 'nested', 'two', 'report.json'), JSON.stringify(game2));
    writeFileSync(join(source, 'nested', 'one', 'report.json'), JSON.stringify(game1));

    const first = buildReportCatalog({ sourceRoot: source, outputRoot: outputA, fixture: false });
    const second = buildReportCatalog({ sourceRoot: source, outputRoot: outputB, fixture: false });

    expect(first.reports.map((entry) => entry.id)).toEqual(['game_001', 'game_002']);
    expect(readFileSync(join(outputA, 'catalog.json'))).toEqual(readFileSync(join(outputB, 'catalog.json')));
    expect(readFileSync(join(outputA, 'bundles', 'game_001', 'report.json'))).toEqual(readFileSync(join(outputB, 'bundles', 'game_001', 'report.json')));
  }, 30_000);

  it('does not replace an existing catalog when a source report is invalid', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-catalog-invalid-'));
    const source = join(root, 'source');
    const output = join(root, 'output');
    mkdirSync(source, { recursive: true });
    mkdirSync(output, { recursive: true });
    writeFileSync(join(output, 'sentinel.txt'), 'keep');
    writeFileSync(join(source, 'report.json'), JSON.stringify({ ...game1, schema_version: 'future' }));

    expect(() => buildReportCatalog({ sourceRoot: source, outputRoot: output, force: true })).toThrow(/schema validation failed/i);
    expect(readFileSync(join(output, 'sentinel.txt'), 'utf8')).toBe('keep');
  });

  it('refuses either direction of source/output overlap before writing', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-catalog-overlap-'));
    const source = join(root, 'source');
    const nestedOutput = join(source, 'public', 'reports');
    mkdirSync(source, { recursive: true });
    writeFileSync(join(source, 'report.json'), JSON.stringify(game1));

    expect(() => buildReportCatalog({ sourceRoot: source, outputRoot: nestedOutput })).toThrow(/must not overlap/i);
    expect(() => buildReportCatalog({ sourceRoot: source, outputRoot: root, force: true })).toThrow(/must not overlap/i);
  });

  it('stages the authorized Phase 4A sample as a two-game non-fixture catalog', () => {
    const repositoryRoot = dirname(dirname(fileURLToPath(import.meta.url)));
    const source = join(repositoryRoot, 'fixtures', 'phase4a_generated');
    const output = join(mkdtempSync(join(tmpdir(), 'viewer-phase4a-catalog-')), 'reports');

    const catalog = buildReportCatalog({ sourceRoot: source, outputRoot: output });

    expect(catalog.reports.map((entry) => entry.id)).toEqual(['game_001', 'game_002']);
    expect(catalog.reports.every((entry) => entry.fixture === false)).toBe(true);
    expect(catalog.reports.map((entry) => entry.title)).toEqual(['Game 1: completed campaign', 'Game 2: completed campaign']);
    expect(catalog.reports.every((entry) => entry.experiment_id === 'xai_grok_4_5_rules_v5_campaign_v1_3_persistent_advanced_roster_round_robin_2x_1910')).toBe(true);
    expect(JSON.stringify(catalog)).not.toMatch(/[A-Za-z]:[\\/]/);
    expect(readFileSync(join(output, 'bundles', 'game_001', 'report.json'))).toEqual(
      readFileSync(join(source, 'reports', 'xai_grok_4_5_rules_v5_campaign_v1_3_persistent_advanced_roster_round_robin_2x_1910', 'game_001', 'omniscient', 'report.json')),
    );
  }, 60_000);

  it('refuses omniscient or legacy input when public-safe staging is required', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-public-safe-catalog-'));
    const unsafeSource = join(root, 'unsafe');
    mkdirSync(unsafeSource, { recursive: true });
    writeFileSync(join(unsafeSource, 'report.json'), JSON.stringify(game1));
    expect(() => buildReportCatalog({ sourceRoot: unsafeSource, outputRoot: join(root, 'unsafe-output'), requirePublicSafe: true })).toThrow(/public-safe catalog refuses/i);

    const repositoryRoot = dirname(dirname(fileURLToPath(import.meta.url)));
    const safeSource = join(repositoryRoot, 'fixtures', 'phase5a_generated');
    const catalog = buildReportCatalog({ sourceRoot: safeSource, outputRoot: join(root, 'safe-output'), requirePublicSafe: true });
    expect(catalog.reports).toHaveLength(2);
    expect(catalog.reports.every((entry) => entry.audience_mode === 'postgame_public' && entry.approved_for_static_public_distribution)).toBe(true);
    expect(readFileSync(join(root, 'safe-output', 'bundles', 'game_001', 'released', 'negotiation_transcript.json'), 'utf8')).toMatch(/postgame_released_negotiation_transcript/);
  }, 60_000);

  it('associates only an explicitly supplied, valid approved narrative', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-approved-narrative-'));
    const source = join(root, 'source');
    const approved = join(root, 'approved');
    const output = join(root, 'output');
    mkdirSync(source, { recursive: true });
    mkdirSync(join(approved, 'game_001'), { recursive: true });
    const reportText = JSON.stringify(game1);
    writeFileSync(join(source, 'report.json'), reportText);
    const reportSha = createHash('sha256').update(reportText).digest('hex');
    const plan = createNarrativePlan(game1, reportSha, 'omniscient_postgame');
    const narrative = createTemplateNarrative(plan, createNarrativeOutline(plan));
    writeFileSync(join(approved, 'game_001', 'approved.json'), JSON.stringify(narrative));

    const catalog = buildReportCatalog({ sourceRoot: source, outputRoot: output, approvedNarrativeRoot: approved });
    expect(catalog.reports[0].narrative).toMatchObject({ report_sha256: reportSha, audience: 'omniscient_postgame' });
    expect(readFileSync(join(output, 'narratives', 'game_001', 'approved.json'), 'utf8')).toBe(JSON.stringify(narrative));
  }, 30_000);
});
