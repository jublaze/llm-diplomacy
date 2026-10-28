import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';

export function publicDemoReport() {
  const source = { source_id: 'demo-orders', artifact_path: 'evidence/orders.json', sha256: createHash('sha256').update('{}\n').digest('hex'), visibility: 'public', kind: 'orders' };
  const years = [{ year: 1901, phases: ['spring', 'fall', 'winter'].map((season) => ({
    phase_id: `1901_${season}_${season === 'winter' ? 'adjustment' : 'movement'}`,
    kind: season === 'winter' ? 'adjustment' : 'movement', year: 1901, season,
    board_before: { supply_ownership: {}, units: [] }, board_after: { supply_ownership: {}, units: [] },
    messages: [], orders: season === 'spring' ? ['demo-hold'] : [], adjudication: {}, private_records: [],
    narrative: { overview: 'Synthetic demonstration only. No model or historical game produced these records.', theater_sections: [], aftermath: '', source_refs: ['demo-orders'] },
    source_refs: ['demo-orders'], extensions: {},
  })) }];
  return {
    schema_version: 'diplomacy_report_bundle_v1', bundle_id: 'synthetic-pages-demo:v1',
    source: { experiment_id: 'synthetic-pages-demo', game_id: 'game_001', artifact_manifest_sha256: '0'.repeat(64), visibility_mode: 'public' },
    map: { map_id: 'standard_v1', geometry_version: 'standard_geometry_v1' },
    game: { title: 'Synthetic viewer demonstration', years_played: [1901, 1901], outcome: { termination: { kind: 'other_recorded_reason', label: 'Synthetic example; no competitive result', final_year: 1901 }, leaders: [], solo_victor: null }, provenance: {} },
    roster: [], summary: {}, strategic_arcs: [], years, messages: {},
    orders: { 'demo-hold': { order_id: 'demo-hold', phase_id: '1901_spring_movement', power: 'France', notation: 'A par H', effective_status: 'succeeds', source_ref: 'demo-orders', payload: {} } },
    private_records: {}, evidence_index: { 'demo-orders': source },
    extensions: { audience: {
      schema_version: 'diplomacy_report_audience_v1', policy_version: 'diplomacy_audience_policy_v1', mode: 'postgame_public',
      reader_description: 'Public synthetic demonstration. No player state, correspondence or model output.', approved_for_static_public_distribution: true,
      projection: { generator: 'build_public_demo', source_bundle_id: 'synthetic-pages-demo', source_bundle_sha256: '0'.repeat(64) },
    } },
  };
}

export function writePublicDemo(destination: string) {
  const root = resolve(destination);
  if (existsSync(root)) throw new Error('Demo destination already exists; choose a new directory.');
  const report = publicDemoReport();
  const errors = [...validateSchema(report), ...validateBundle(report)];
  if (errors.length) throw new Error(errors.join('; '));
  mkdirSync(join(root, 'evidence'), { recursive: true });
  writeFileSync(join(root, 'evidence/orders.json'), '{}\n');
  writeFileSync(join(root, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const destination = process.argv[2];
  if (!destination) throw new Error('Usage: build_public_demo.ts <new-output-directory>');
  writePublicDemo(destination);
}
