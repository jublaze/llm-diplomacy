import test from 'node:test'; import assert from 'node:assert/strict'; import { readFileSync } from 'node:fs'; import { spawnSync } from 'node:child_process';
import { validateBundle } from '../src/report-model/validate.ts'; import { validateSchema } from '../src/report-model/validate-schema.ts'; import { normalizeOutcome } from '../src/report-model/outcome.ts';
const fixture = JSON.parse(readFileSync('fixtures/game_001/report.json', 'utf8')); const clone = () => structuredClone(fixture);
const cases = JSON.parse(readFileSync('tests/outcome-policy-cases.json', 'utf8'));
test('shared table drives identical TypeScript and Python outcome policy', () => {
  const python = spawnSync('python', ['scripts/verify_outcome_policy.py'], { encoding: 'utf8' }); assert.equal(python.status, 0, python.stderr);
  const byName = new Map(JSON.parse(python.stdout).map((item: any) => [item.name, item.outcome]));
  for (const item of cases) { const cutoff = item.expected_kind === 'scheduled_max_year' ? item.final_year : undefined; const ts = normalizeOutcome(item.raw, item.final_year, cutoff); assert.deepEqual(ts, byName.get(item.name)); assert.equal(ts.termination.kind, item.expected_kind); assert.equal(ts.solo_victor?.power ?? null, item.solo_victor); }
});
test('real fixtures frame leaders as scheduled-cutoff leaders', () => { const game2=JSON.parse(readFileSync('fixtures/game_002/report.json','utf8')); for (const game of [fixture,game2]) assert.deepEqual(normalizeOutcome(game.game.provenance.raw_outcome,game.game.outcome.termination.final_year,game.game.outcome.termination.final_year),game.game.outcome); assert.deepEqual(fixture.game.outcome.leaders,[{agent_id:'shaver',power:'russia',supply_centers:12}]); assert.deepEqual(game2.game.outcome.leaders,[{agent_id:'major',power:'russia',supply_centers:10}]); assert.equal(fixture.game.outcome.solo_victor,null); assert.equal(game2.game.outcome.solo_victor,null); });
test('schema and semantic validators are complementary', () => { const a=clone(); delete a.bundle_id; assert.match(validateSchema(a).join('\n'), /bundle_id/); const b=clone(); b.years[0].phases[0].year=1902; assert.match(validateBundle(b).join('\n'), /must equal parent year/); });
test('evidence paths and visibility are checked', () => { const a=clone(); a.evidence_index[Object.keys(a.evidence_index)[0]].artifact_path='../escape'; assert.match(validateBundle(a).join('\n'), /artifact_path/); const b=clone(); const id=Object.keys(b.private_records)[0]; b.evidence_index[b.private_records[id].source_ref].visibility='public'; assert.match(validateBundle(b).join('\n'), /private record evidence/); });
test('Phase 1.1 editorial claims resolve to the correct evidence target', () => { const editorial=fixture.years.flatMap((year:any)=>year.phases).filter((phase:any)=>phase.year===1902 && phase.narrative.editorial); for(const phase of editorial){ for(const section of phase.narrative.editorial.sections){ for(const item of [...section.claims,section.bridge]){ for(const citation of item.citations){ assert.ok(fixture.evidence_index[citation.source_ref]); if(citation.target.type==='message') assert.equal(fixture.messages[citation.target.id].source_ref,citation.source_ref); if(citation.target.type==='record') assert.equal(fixture.private_records[citation.target.id].source_ref,citation.source_ref); if(citation.target.type==='order') assert.equal(fixture.orders[citation.target.id].source_ref,citation.source_ref); } } } } const a=clone(); a.years.find((year:any)=>year.year===1902).phases.find((phase:any)=>phase.phase_id==='1902_spring_movement').narrative.editorial.sections[0].claims[0].citations[0].source_ref='missing'; assert.match(validateBundle(a).join('\n'), /must resolve to evidence/); });

test('semantic year coverage follows the declared duration and outcome', () => {
  const short = clone();
  short.years = short.years.filter((year: any) => year.year === 1901);
  short.game.years_played = [1901, 1901];
  short.game.outcome.termination.final_year = 1901;
  assert.deepEqual(validateBundle(short), []);

  const fullHistorical = clone();
  assert.deepEqual(validateBundle(fullHistorical), []);

  const gap = clone();
  gap.years = gap.years.filter((year: any) => year.year !== 1905);
  assert.match(validateBundle(gap).join('\n'), /must follow the previous played year/);

  const outOfRange = clone();
  outOfRange.years.push({ year: 1911, phases: [] });
  assert.match(validateBundle(outOfRange).join('\n'), /outside the declared played-year range/);

  const invalidDuration = clone();
  invalidDuration.game.years_played = [1910, 1901];
  assert.match(validateBundle(invalidDuration).join('\n'), /ascending range/);

  const invalidOutcome = clone();
  invalidOutcome.game.outcome.termination.final_year = 1909;
  assert.match(validateBundle(invalidOutcome).join('\n'), /must equal the declared final played year/);
});
