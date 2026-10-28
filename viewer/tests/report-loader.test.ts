import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import {
  ReportLoadError,
  createInMemoryCatalog,
  loadCatalog,
  loadCatalogReport,
  readerTitle,
  validateCatalog,
  type FetchLike,
} from '../src/report-loader';

const jsonResponse = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 200 ? 'OK' : 'Not Found',
  text: async () => JSON.stringify(body),
});

describe('Phase 4B report catalog loader', () => {
  it('selects generic reader titles without parsing or special-casing experiment identifiers', () => {
    const arbitraryTechnical = structuredClone(game1) as any;
    arbitraryTechnical.source.experiment_id = 'arbitrary_future_experiment';
    arbitraryTechnical.source.game_id = 'game_042';
    arbitraryTechnical.game.title = 'arbitrary_future_experiment / game_042';
    expect(readerTitle(arbitraryTechnical)).toBe('Game 42: completed campaign');

    const authored = structuredClone(arbitraryTechnical) as any;
    authored.game.title = 'The Alpine Settlement';
    expect(readerTitle(authored)).toBe('The Alpine Settlement');

    arbitraryTechnical.source.experiment_id = 'not_a_current_model_or_fixture';
    arbitraryTechnical.game.title = 'not_a_current_model_or_fixture / game_042';
    expect(readerTitle(arbitraryTechnical)).toBe('Game 42: completed campaign');
  });

  it('creates a stable sorted fixture catalog without embedding report data', () => {
    const data = createInMemoryCatalog({ game_002: game2, game_001: game1 }, { fixture: true });

    expect(data.catalog.schema_version).toBe('diplomacy_report_catalog_v1');
    expect(data.catalog.reports.map((entry) => entry.id)).toEqual(['game_001', 'game_002']);
    expect(data.catalog.reports[0]).toMatchObject({
      label: 'Game 1',
      fixture: true,
      map_id: 'standard_v1',
      experiment_id: game1.source.experiment_id,
      artifact_manifest_sha256: game1.source.artifact_manifest_sha256,
    });
    expect(JSON.stringify(data.catalog)).not.toContain('messages');
  });

  it('rejects malformed, duplicate, absolute, and unsupported catalog entries', () => {
    expect(() => validateCatalog({ schema_version: 'future', reports: [] })).toThrow(/unsupported catalog schema/i);
    const valid = createInMemoryCatalog({ game_001: game1 }).catalog;
    expect(() => validateCatalog({ ...valid, reports: [valid.reports[0], valid.reports[0]] })).toThrow(/duplicate report id/i);
    expect(() => validateCatalog({ ...valid, reports: [{ ...valid.reports[0], report_url: 'C:\\raw\\report.json' }] })).toThrow(/relative report_url/i);
    expect(() => validateCatalog({ ...valid, reports: [{ ...valid.reports[0], title: 'C:\\raw\\game_001' }] })).toThrow(/absolute local path/i);
    expect(() => validateCatalog({ ...valid, reports: [{ ...valid.reports[0], experiment_id: '../escape' }] })).toThrow(/invalid experiment_id/i);
    expect(() => validateCatalog({ ...valid, reports: [{ ...valid.reports[0], id: '../escape' }] })).toThrow(/unsafe report id/i);
    expect(() => validateCatalog({ ...valid, reports: [{ ...valid.reports[0], sha256: 'not-a-checksum' }] })).toThrow(/invalid sha256/i);
  });

  it('resolves a selected report relative to its catalog and validates both contracts', async () => {
    const catalog = createInMemoryCatalog({ game_001: game1 }).catalog;
    const calls: string[] = [];
    const fetcher: FetchLike = async (url) => {
      calls.push(url);
      if (url === 'https://viewer.test/reports/catalog.json') return jsonResponse(catalog);
      if (url === 'https://viewer.test/reports/bundles/game_001/report.json') return jsonResponse(game1);
      return jsonResponse({}, 404);
    };

    const loadedCatalog = await loadCatalog('/reports/catalog.json', fetcher, 'https://viewer.test/');
    const report = await loadCatalogReport(loadedCatalog.catalog.reports[0], loadedCatalog.url, fetcher);

    expect(report.bundle_id).toBe(game1.bundle_id);
    expect(calls).toEqual([
      'https://viewer.test/reports/catalog.json',
      'https://viewer.test/reports/bundles/game_001/report.json',
    ]);
  });

  it('reports HTTP, JSON, schema, semantic, and catalog/report identity failures', async () => {
    const catalog = createInMemoryCatalog({ game_001: game1 }).catalog;
    const entry = catalog.reports[0];
    await expect(loadCatalog('/missing.json', async () => jsonResponse({}, 404), 'https://viewer.test/')).rejects.toThrow(/catalog request failed.*404/i);
    await expect(loadCatalog('/bad.json', async () => ({ ...jsonResponse({}), text: async () => '{' }), 'https://viewer.test/')).rejects.toThrow(/valid json/i);
    await expect(loadCatalogReport(entry, 'https://viewer.test/reports/catalog.json', async () => jsonResponse({ ...game1, schema_version: 'future' }))).rejects.toThrow(/report schema validation failed/i);
    const broken = structuredClone(game1) as any;
    delete broken.evidence_index[Object.keys(broken.evidence_index)[0]];
    await expect(loadCatalogReport(entry, 'https://viewer.test/reports/catalog.json', async () => jsonResponse(broken))).rejects.toThrow(/semantic validation failed/i);
    await expect(loadCatalogReport({ ...entry, bundle_id: 'other' }, 'https://viewer.test/reports/catalog.json', async () => jsonResponse(game1))).rejects.toBeInstanceOf(ReportLoadError);
    await expect(loadCatalogReport({ ...entry, sha256: '0'.repeat(64) }, 'https://viewer.test/reports/catalog.json', async () => jsonResponse(game1))).rejects.toThrow(/checksum mismatch/i);
  });
});
