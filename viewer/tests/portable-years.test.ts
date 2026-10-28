import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { validateBundle } from '../src/report-model/validate.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { normalizeOutcome } from '../src/report-model/outcome.ts';
import { catalogEntryFromReport, loadCatalogReport, validateCatalog } from '../src/report-loader.ts';
import { defaultPhase } from '../src/viewer-model.ts';
import { archiveNavigationTarget } from '../src/archive-navigation.ts';
import { syntheticFallSolo, syntheticReport as report } from './synthetic-report.ts';

test('synthetic short, post-1910, and long reports pass schema and semantic validation', () => {
  for (const [start, end] of [[1901, 1901], [1901, 1912], [1912, 1930]] as const) {
    const value = report(start, end);
    assert.deepEqual(validateSchema(value), []);
    assert.deepEqual(validateBundle(value), []);
    assert.deepEqual(value.game.years_played, [start, end]);
    assert.equal(defaultPhase(value), `${start}_spring_movement`);
  }
});

test('coherent Fall solos may end without Winter at early and extended years', () => {
  for (const finalYear of [1901, 1912]) {
    const value = syntheticFallSolo(report(1901, finalYear), finalYear) as any;
    assert.deepEqual(validateSchema(value), []);
    assert.deepEqual(validateBundle(value), []);

    const multiPowerRoster = structuredClone(value);
    assert.deepEqual(validateBundle(multiPowerRoster), []);

    const badLeaders = structuredClone(value);
    badLeaders.game.outcome.leaders = [{ agent_id: 'agent_germany', power: 'germany', supply_centers: 18 }];
    assert.match(validateBundle(badLeaders).join('\n'), /outcome\/leaders: must contain only the evidenced solo victor/);

    const badRawCounts = structuredClone(value);
    badRawCounts.game.provenance.raw_outcome.country_results.germany.owned_centers = 19;
    assert.match(validateBundle(badRawCounts).join('\n'), /country_results\/germany: must match final board ownership/);
  }
});

test('solo validation preserves null neutral centers and rejects other non-string owners', () => {
  const neutral = syntheticFallSolo(report(1901, 1901), 1901) as any;
  neutral.years[0].phases.at(-1).board_after.supply_ownership.neutral = null;
  neutral.years[0].phases.at(-1).adjudication.output_state.supply_ownership.neutral = null;
  assert.deepEqual(validateBundle(neutral), []);

  const invalid = syntheticFallSolo(report(1901, 1901), 1901) as any;
  invalid.years[0].phases.at(-1).board_after.supply_ownership.invalid = false;
  invalid.years[0].phases.at(-1).adjudication.output_state.supply_ownership.invalid = false;
  assert.match(validateBundle(invalid).join('\n'), /must match the final Fall board/);
});

test('solo finality follows canonical chronology and supports a resolved Fall retreat', () => {
  const winterBeforeFall = syntheticFallSolo(report(1901, 1901), 1901) as any;
  const phases = report(1901, 1901).years[0].phases;
  const winter = phases.find((phase) => phase.season === 'winter')!;
  winterBeforeFall.years[0].phases.splice(1, 0, winter);
  assert.match(validateBundle(winterBeforeFall).join('\n'), /chronological order used by the reader/);

  const retreatAfterDeclaredMovement = syntheticFallSolo(report(1901, 1901), 1901) as any;
  const movement = retreatAfterDeclaredMovement.years[0].phases.at(-1);
  retreatAfterDeclaredMovement.years[0].phases.push({
    ...structuredClone(movement), phase_id: '1901_fall_retreat', kind: 'retreat',
  });
  assert.match(validateBundle(retreatAfterDeclaredMovement).join('\n'), /must identify the final Fall movement or retreat phase/);

  const retreatSolo = syntheticFallSolo(report(1901, 1901), 1901) as any;
  const fallMovement = retreatSolo.years[0].phases.at(-1);
  const fallRetreat = { ...structuredClone(fallMovement), phase_id: '1901_fall_retreat', kind: 'retreat' };
  retreatSolo.years[0].phases.push(fallRetreat);
  retreatSolo.game.outcome.termination.final_phase_id = '1901_fall_retreat';
  assert.deepEqual(validateSchema(retreatSolo), []);
  assert.deepEqual(validateBundle(retreatSolo), []);
});

test('solo metadata cannot skip Winter without final Fall board and assigned-power evidence', () => {
  const value = report(1901, 1901);
  value.years[0].phases = value.years[0].phases.filter((phase) => phase.season !== 'winter');
  value.game.outcome.termination.kind = 'solo_victory';
  (value.game.outcome.termination as any).final_phase_id = '1901_fall_movement';
  value.game.outcome.solo_victor = { agent_id: 'agent_france', power: 'france', supply_centers: 18 };
  assert.match(validateBundle(value).join('\n'), /must match the final Fall board and assigned roster power/);

  const pending = structuredClone(value);
  pending.roster = [{ agent_id: 'agent_france', power: 'france' }];
  pending.years[0].phases.at(-1)!.board_after.supply_ownership = Object.fromEntries(Array.from({ length: 18 }, (_, index) => [`center_${index}`, 'france']));
  const pendingUnits = [{ owner: 'germany', status: 'dislodged', must_retreat: true }];
  pending.years[0].phases.at(-1)!.board_after.units = pendingUnits;
  pending.years[0].phases.at(-1)!.adjudication = { output_state: { supply_ownership: pending.years[0].phases.at(-1)!.board_after.supply_ownership, units: pendingUnits } };
  assert.match(validateBundle(pending).join('\n'), /cannot be declared while units still require retreat resolution/);

  const mismatchedResolution = structuredClone(value);
  mismatchedResolution.years[0].phases.at(-1)!.board_after.supply_ownership = Object.fromEntries(Array.from({ length: 18 }, (_, index) => [`center_${index}`, 'france']));
  mismatchedResolution.years[0].phases.at(-1)!.adjudication = { output_state: { supply_ownership: { paris: 'france' }, units: [] } };
  mismatchedResolution.roster = [{ agent_id: 'agent_france', power: 'france' }];
  assert.match(validateBundle(mismatchedResolution).join('\n'), /adjudication\/output_state: must agree with the final snapshot/);

  const cutoff = report(1901, 1910);
  cutoff.years.at(-1)!.phases = cutoff.years.at(-1)!.phases.filter((phase) => phase.season !== 'winter');
  assert.match(validateBundle(cutoff).join('\n'), /missing winter adjustment phase/);
});

test('archive navigation target follows the report default phase for short, extended, and legacy durations', () => {
  for (const value of [report(1901, 1901), report(1901, 1912), report(1901, 1910)]) {
    const target = archiveNavigationTarget(value);
    assert.equal(target.year, 1901);
    assert.equal(target.phaseId, '1901_spring_movement');
    assert.equal(target.chapterButton, 'Read 1901 chapter');
    assert.equal(target.heading, '1901: campaign record');
  }
});

test('year validation rejects absent, duplicate, out-of-range, reversed, and contradictory evidence', () => {
  const missing = report(1901, 1902); missing.years.pop();
  assert.match(validateBundle(missing).join('\n'), /exactly one entry for every declared played year/);
  for (const season of ['spring', 'fall', 'winter']) {
    const incomplete = report(1901, 1901);
    incomplete.years[0].phases = incomplete.years[0].phases.filter((phase) => phase.season !== season);
    const requiredKind = season === 'winter' ? 'adjustment' : 'movement';
    assert.match(validateBundle(incomplete).join('\n'), new RegExp(`missing ${season} ${requiredKind} phase`));
  }
  const duplicate = report(1901, 1902); duplicate.years[1] = structuredClone(duplicate.years[0]);
  assert.match(validateBundle(duplicate).join('\n'), /duplicates a declared year/);
  const outOfRange = report(1901, 1901); outOfRange.years[0].year = 1912;
  assert.match(validateBundle(outOfRange).join('\n'), /outside the declared played-year range/);
  const reversed = report(1902, 1901);
  assert.match(validateBundle(reversed).join('\n'), /ascending range/);
  const contradictory = report(1901, 1902); contradictory.game.outcome.termination.final_year = 1901;
  assert.match(validateBundle(contradictory).join('\n'), /must equal the declared final played year/);
  const unsafeSpan = report(1901, 1901);
  unsafeSpan.game.years_played[1] = Number.MAX_SAFE_INTEGER;
  unsafeSpan.game.outcome.termination.final_year = Number.MAX_SAFE_INTEGER;
  assert.ok(validateBundle(unsafeSpan).some((error) => error.includes('exactly one entry')));
});

test('phase IDs, semantic identity, and normalized season kinds agree', () => {
  const mutations: Array<[string, (value: ReturnType<typeof report>) => void, RegExp]> = [
    ['numeric phase ID', (value) => { (value.years[0].phases[0] as any).phase_id = 123; }, /phase_id/],
    ['ID year differs from parent', (value) => { (value.years[0].phases[0] as any).phase_id = '1912_spring_movement'; }, /must agree with the parent year/],
    ['phase year differs from parent', (value) => { (value.years[0].phases[0] as any).year = 1912; }, /must equal parent year/],
    ['unknown season', (value) => { (value.years[0].phases[0] as any).season = 'summer'; }, /season/],
    ['unknown kind', (value) => { (value.years[0].phases[0] as any).kind = 'negotiation'; }, /kind/],
    ['winter movement', (value) => { const winter = value.years[0].phases.find((phase) => phase.season === 'winter')! as any; winter.kind = 'movement'; winter.phase_id = '1901_winter_movement'; }, /not a normalized phase kind/],
    ['duplicate semantic movement', (value) => { const duplicate = structuredClone(value.years[0].phases.find((phase) => phase.season === 'spring')!) as any; duplicate.phase_id = '1901_spring_movement_copy'; value.years[0].phases.push(duplicate); }, /duplicates semantic phase spring movement/],
    ['retreat replaces required movement', (value) => { const spring = value.years[0].phases.find((phase) => phase.season === 'spring')! as any; spring.kind = 'retreat'; spring.phase_id = '1901_spring_retreat'; }, /missing spring movement phase/],
  ];
  for (const [name, mutate, semanticError] of mutations) {
    const value = report(1901, 1901);
    mutate(value);
    const schemaErrors = validateSchema(value);
    const semanticErrors = validateBundle(value);
    assert.ok(schemaErrors.length > 0 || semanticErrors.length > 0, `${name} should be rejected`);
    assert.match(semanticErrors.join('\n'), semanticError, name);
  }

  const withRetreats = report(1901, 1901);
  for (const season of ['spring', 'fall']) {
    const movement = withRetreats.years[0].phases.find((phase) => phase.season === season)!;
    const movementIndex = withRetreats.years[0].phases.indexOf(movement);
    withRetreats.years[0].phases.splice(movementIndex + 1, 0, {
      ...structuredClone(movement), phase_id: `1901_${season}_retreat`, kind: 'retreat',
    });
  }
  assert.deepEqual(validateSchema(withRetreats), []);
  assert.deepEqual(validateBundle(withRetreats), []);
});

test('catalog and loader preserve a 1912 final year end to end', async () => {
  const value = report(1901, 1912);
  const text = JSON.stringify(value);
  const entry = catalogEntryFromReport('game_001', value as any, { sha256: createHash('sha256').update(text).digest('hex') });
  const catalog = validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: [entry] });
  assert.deepEqual(catalog.reports[0].years_played, [1901, 1912]);
  const fetcher = async (url: string) => ({ ok: true, status: 200, statusText: 'OK', text: async () => url.endsWith('/report.json') ? text : '' });
  const loaded = await loadCatalogReport(catalog.reports[0], 'https://example.test/catalog.json', fetcher);
  assert.deepEqual(loaded.game.years_played, [1901, 1912]);
});

test('a scheduled cutoff is classified only when explicitly declared', () => {
  const raw = { result: 'completed', country_results: { france: { agent_id: 'f', owned_centers: 12 } } };
  assert.equal(normalizeOutcome(raw, 1912).termination.kind, 'other_recorded_reason');
  assert.equal(normalizeOutcome(raw, 1912, 1912).termination.kind, 'scheduled_max_year');
});

test('shared outcome-policy cases retain explicit cutoff evidence', () => {
  const cases = JSON.parse(readFileSync(new URL('./outcome-policy-cases.json', import.meta.url), 'utf8'));
  for (const item of cases) {
    const cutoff = item.expected_kind === 'scheduled_max_year' ? item.final_year : undefined;
    assert.equal(normalizeOutcome(item.raw, item.final_year, cutoff).termination.kind, item.expected_kind, item.name);
  }
});
