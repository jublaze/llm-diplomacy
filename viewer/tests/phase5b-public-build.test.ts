// @vitest-environment node
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import legacyReport from '../fixtures/game_001/report.json';
import { stagePublicReports, verifyPublicBuild } from '../scripts/public_build';

const repositoryRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const phase5aRoot = join(repositoryRoot, 'fixtures', 'phase5a_generated');
const phase4aRoot = join(repositoryRoot, 'fixtures', 'phase4a_generated');
const researchCatalog = join(repositoryRoot, 'public', 'reports', 'catalog.json');
const hash = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');

describe('Phase 5B public build boundary', () => {
  it('stages the Phase 5A collection deterministically without changing the research catalog', async () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-phase5b-stage-'));
    const before = hash(researchCatalog);
    const first = join(root, 'first');
    const second = join(root, 'second');

    const auditA = stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: first });
    const auditB = stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: second });

    expect(auditA).toEqual(auditB);
    expect(auditA).toMatchObject({
      schema_version: 'diplomacy_public_stage_audit_v1',
      audience_mode: 'postgame_public',
      audience_policy_version: 'diplomacy_audience_policy_v1',
      bundle_count: 2,
      error_count: 0,
    });
    expect(readFileSync(join(first, 'reports', 'catalog.json'))).toEqual(readFileSync(join(second, 'reports', 'catalog.json')));
    expect(readFileSync(join(first, 'public-build-audit.json'))).toEqual(readFileSync(join(second, 'public-build-audit.json')));
    expect(hash(researchCatalog)).toBe(before);
    expect(() => stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: first })).toThrow(/already exists/i);
  }, 120_000);

  it('rejects legacy, omniscient, mixed, malformed, overlapping, raw-experiment, and catalog-mismatched inputs', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-phase5b-reject-'));
    const legacy = join(root, 'legacy');
    mkdirSync(legacy, { recursive: true });
    writeFileSync(join(legacy, 'report.json'), JSON.stringify(legacyReport));
    expect(() => stagePublicReports({ sourceRoot: legacy, outputRoot: join(root, 'legacy-out') })).toThrow(/postgame_public|public-safe/i);

    const mixed = join(root, 'mixed');
    cpSync(phase5aRoot, mixed, { recursive: true });
    mkdirSync(join(mixed, 'unsafe'), { recursive: true });
    writeFileSync(join(mixed, 'unsafe', 'report.json'), JSON.stringify(legacyReport));
    expect(() => stagePublicReports({ sourceRoot: mixed, outputRoot: join(root, 'mixed-out') })).toThrow(/postgame_public|public-safe/i);

    const malformed = join(root, 'malformed');
    mkdirSync(malformed, { recursive: true });
    writeFileSync(join(malformed, 'report.json'), '{');
    expect(() => stagePublicReports({ sourceRoot: malformed, outputRoot: join(root, 'malformed-out') })).toThrow(/valid JSON/i);

    expect(() => stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: join(phase5aRoot, 'nested') })).toThrow(/overlap/i);

    const raw = join(root, 'experiments', 'completed-game');
    mkdirSync(raw, { recursive: true });
    expect(() => stagePublicReports({ sourceRoot: raw, outputRoot: join(root, 'raw-out') })).toThrow(/raw experiment/i);
    expect(() => stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: join(root, 'experiments', 'unsafe-output') })).toThrow(/raw experiment/i);
    expect(() => stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: join(repositoryRoot, 'public', 'reports') })).toThrow(/research catalog/i);

    const catalogTemplate = join(root, 'catalog-template');
    stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: catalogTemplate });
    const mismatch = join(root, 'mismatch');
    cpSync(join(catalogTemplate, 'reports'), mismatch, { recursive: true });
    const mismatchedCatalog = JSON.parse(readFileSync(join(mismatch, 'catalog.json'), 'utf8'));
    mismatchedCatalog.reports[0].bundle_id = 'deliberately-mismatched-bundle';
    writeFileSync(join(mismatch, 'catalog.json'), JSON.stringify(mismatchedCatalog));
    expect(() => stagePublicReports({ sourceRoot: mismatch, outputRoot: join(root, 'mismatch-out') })).toThrow(/catalog\/report identity mismatch/i);
  }, 120_000);

  it('verifies emitted files, loader closure, retained public facts, and forbidden-source leakage', async () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-phase5b-verify-'));
    const dist = join(root, 'dist-public');
    stagePublicReports({ sourceRoot: phase5aRoot, outputRoot: dist });
    writeFileSync(join(dist, 'index.html'), '<div id="root"></div><script type="module" src="./assets/app.js"></script>');
    mkdirSync(join(dist, 'assets'), { recursive: true });
    writeFileSync(join(dist, 'assets', 'app.js'), 'document.documentElement.dataset.build="postgame_public";');

    const audit = await verifyPublicBuild({ distRoot: dist, forbiddenSourceRoot: phase4aRoot });
    expect(audit).toMatchObject({
      schema_version: 'diplomacy_public_build_audit_v1',
      bundle_count: 2,
      private_record_count: 0,
      omniscient_evidence_count: 0,
      unresolved_reference_count: 0,
      forbidden_id_match_count: 0,
      absolute_path_count: 0,
      loader_error_count: 0,
      error_count: 0,
    });
    expect(audit.order_count).toBeGreaterThan(0);
    expect(audit.adjudication_count).toBeGreaterThan(0);
    expect(audit.released_message_count).toBeGreaterThan(0);

    writeFileSync(join(dist, 'assets', 'leak.js'), 'PHASE_5B_PRIVATE_TEXT_SENTINEL C:\\Users\\secret\\raw.json');
    const leaked = await verifyPublicBuild({ distRoot: dist, forbiddenSourceRoot: phase4aRoot });
    expect(leaked.private_sentinel_count).toBe(1);
    expect(leaked.absolute_path_count).toBe(1);
    expect(leaked.error_count).toBeGreaterThan(0);
  }, 120_000);
});
