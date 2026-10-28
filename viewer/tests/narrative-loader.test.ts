import game1 from '../fixtures/game_001/report.json';
import { createNarrativeOutline, createNarrativePlan, createTemplateNarrative } from '../src/narrative-generator';
import { loadCatalogNarrative, loadCatalogReport, validateCatalog, type FetchLike } from '../src/report-loader';

const response = (text: string, status = 200) => ({ ok: status === 200, status, statusText: status === 200 ? 'OK' : 'Not Found', text: async () => text });
const sha256 = async (text: string) => {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
};

describe('Phase 4C catalog narrative overlays', () => {
  it('loads a separately checksummed and contract-valid overlay', async () => {
    const reportText = JSON.stringify(game1);
    const reportSha256 = await sha256(reportText);
    const plan = createNarrativePlan(game1, reportSha256, 'omniscient_postgame');
    const narrative = createTemplateNarrative(plan, createNarrativeOutline(plan));
    const narrativeText = JSON.stringify(narrative);
    const entry = validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: [{
      id: 'game_001', bundle_id: game1.bundle_id, label: 'Game 1', title: game1.game.title,
      years_played: game1.game.years_played, outcome_label: game1.game.outcome.termination.label,
      schema_version: game1.schema_version, audience: 'omniscient_postgame', map_id: game1.map.map_id,
      report_url: 'bundles/game_001/report.json', sha256: reportSha256, fixture: true,
      narrative: { schema_version: 'historian_narrative_v1', narrative_url: 'narratives/game_001/approved.json', sha256: await sha256(narrativeText), report_sha256: reportSha256, audience: 'omniscient_postgame' },
    }] }).reports[0];
    const fetcher: FetchLike = async (url) => url.includes('/narratives/') ? response(narrativeText) : response(reportText);

    const report = await loadCatalogReport(entry, 'https://viewer.test/reports/catalog.json', fetcher);
    const loaded = await loadCatalogNarrative(entry, 'https://viewer.test/reports/catalog.json', fetcher, report);
    expect(loaded.narrative_id).toBe(narrative.narrative_id);
  });

  it('rejects overlay audience and report-checksum mismatches', async () => {
    const reportText = JSON.stringify(game1);
    const reportSha256 = await sha256(reportText);
    const plan = createNarrativePlan(game1, reportSha256, 'omniscient_postgame');
    const narrative = createTemplateNarrative(plan, createNarrativeOutline(plan));
    narrative.audience = 'public';
    const narrativeText = JSON.stringify(narrative);
    const entry = validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: [{
      id: 'game_001', bundle_id: game1.bundle_id, label: 'Game 1', title: game1.game.title,
      years_played: game1.game.years_played, outcome_label: game1.game.outcome.termination.label,
      schema_version: game1.schema_version, audience: 'omniscient_postgame', map_id: game1.map.map_id,
      report_url: 'bundles/game_001/report.json', sha256: reportSha256, fixture: true,
      narrative: { schema_version: 'historian_narrative_v1', narrative_url: 'narratives/game_001/approved.json', sha256: await sha256(narrativeText), report_sha256: reportSha256, audience: 'omniscient_postgame' },
    }] }).reports[0];
    const fetcher: FetchLike = async () => response(narrativeText);
    await expect(loadCatalogNarrative(entry, 'https://viewer.test/reports/catalog.json', fetcher, game1)).rejects.toThrow(/audience/i);
    expect(() => validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: [{ ...entry, narrative: { ...entry.narrative!, report_sha256: '0'.repeat(64) } }] })).toThrow(/checksum binding/i);
  });
});
