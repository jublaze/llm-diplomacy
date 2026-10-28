// @vitest-environment node
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { projectReport } from '../src/audience-projection';
import { buildComparison } from '../src/comparison-model';
import { createInMemoryCatalog } from '../src/report-loader';
import type { Report } from '../src/viewer-model';

const reports = { game_001: game1 as unknown as Report, game_002: game2 as unknown as Report };
const inputs = () => {
  const cloned = structuredClone(reports);
  const data = createInMemoryCatalog(cloned, { fixture: true });
  return data.catalog.reports.map((entry) => ({ entry, report: cloned[entry.id as keyof typeof cloned] }));
};

describe('Phase 5B descriptive comparison model', () => {
  it('derives the approved Game 1/Game 2 fields in stable order with provenance links', () => {
    const comparison = buildComparison(inputs());
    expect(comparison.issues).toEqual([]);
    expect(comparison.metrics.map((row) => row.key)).toEqual([
      'experiment_id', 'game_id', 'schema_version', 'map_version', 'years_played', 'termination',
      'leaders', 'phase_count', 'active_units', 'public_messages', 'submitted_orders',
      'effective_orders', 'provider_calls', 'estimated_cost',
    ]);
    expect(comparison.metrics.find((row) => row.key === 'phase_count')!.cells.map((cell) => cell.display)).toEqual(['42', '38']);
    expect(comparison.metrics.find((row) => row.key === 'public_messages')!.cells.map((cell) => cell.display)).toEqual(['79', '1']);
    expect(comparison.metrics.find((row) => row.key === 'submitted_orders')!.cells.map((cell) => cell.display)).toEqual(['697', '683']);
    expect(comparison.metrics.find((row) => row.key === 'effective_orders')!.cells.map((cell) => cell.display)).toEqual(['689', '676']);
    expect(comparison.metrics.find((row) => row.key === 'provider_calls')!.cells.map((cell) => cell.display)).toEqual(['1,554', '1,554']);
    expect(comparison.powers.find((row) => row.power === 'russia')!.cells[0].display).toBe('4 → 12 (+8)');
    expect(comparison.metrics[0].cells[0].provenance[0].href).toMatch(/^#game=game_001/);
    expect(comparison.metrics.find((row) => row.key === 'leaders')!.cells[1].provenance[0].phase_id).toBe('1910_winter_adjustment');
  });

  it('labels incompatible maps/schema majors and missing recorded metrics', () => {
    const values = inputs();
    (values[1].report as any).map.map_id = 'variant_v1';
    (values[1].report as any).schema_version = 'diplomacy_report_bundle_v2';
    delete (values[1].report.game as any).cost_and_call_metadata;
    const comparison = buildComparison(values);
    expect(comparison.compatible).toBe(false);
    expect(comparison.issues.map((issue) => issue.kind)).toEqual(expect.arrayContaining(['map', 'schema', 'missing']));
    expect(comparison.metrics.find((row) => row.key === 'provider_calls')!.cells[1]).toMatchObject({ status: 'missing', display: 'Not recorded' });
  });

  it('uses only public message totals and visibly rejects mixed audiences', () => {
    const publicReports = {
      'game_001--public': projectReport(reports.game_001, 'public'),
      'game_002--public': projectReport(reports.game_002, 'public'),
    };
    const publicData = createInMemoryCatalog(publicReports, { fixture: true });
    const publicComparison = buildComparison(publicData.catalog.reports.map((entry) => ({ entry, report: publicReports[entry.id as keyof typeof publicReports] })));
    expect(publicComparison.metrics.find((row) => row.key === 'public_messages')!.cells.map((cell) => cell.display)).toEqual(['79', '1']);
    expect(publicComparison.metrics.find((row) => row.key === 'submitted_orders')!.cells.map((cell) => cell.display)).toEqual(['697', '683']);
    expect(publicComparison.metrics.find((row) => row.key === 'estimated_cost')!.cells.every((cell) => cell.status === 'missing')).toBe(true);

    const mixed = inputs();
    mixed[1] = { ...mixed[1], report: projectReport(reports.game_002, 'public'), entry: publicData.catalog.reports[1] };
    expect(buildComparison(mixed).issues.some((issue) => issue.kind === 'audience')).toBe(true);
  });
});
