import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import game1 from '../fixtures/game_001/report.json';
import { buildNarrativePackage, processFixtureProviderResponse } from '../scripts/build_narratives';

const sha = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');

describe('Phase 4C safe narrative pipeline', () => {
  it('rebuilds deterministic narrative-only outputs without changing factual report bytes', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-narratives-'));
    const reportPath = join(root, 'report.json');
    const outputA = join(root, 'narratives-a');
    const outputB = join(root, 'narratives-b');
    writeFileSync(reportPath, JSON.stringify(game1));
    const before = sha(reportPath);

    buildNarrativePackage({ reportPath, outputRoot: outputA, audience: 'omniscient_postgame' });
    buildNarrativePackage({ reportPath, outputRoot: outputB, audience: 'omniscient_postgame' });

    expect(sha(reportPath)).toBe(before);
    for (const file of ['plan.json', 'outline.json', 'template-candidate.json', 'request.json']) {
      expect(readFileSync(join(outputA, 'game_001', file))).toEqual(readFileSync(join(outputB, 'game_001', file)));
    }
  }, 30_000);

  it('accepts only a valid fixture-provider candidate and quarantines invalid raw output without replacement', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-provider-'));
    const reportPath = join(root, 'report.json');
    const outputRoot = join(root, 'narratives');
    writeFileSync(reportPath, JSON.stringify(game1));
    const built = buildNarrativePackage({ reportPath, outputRoot, audience: 'omniscient_postgame' });

    processFixtureProviderResponse({ reportPath, outputRoot, request: built.request, rawResponse: built.template, runId: 'valid-run' });
    const candidatePath = join(outputRoot, 'game_001', 'provider-candidate.json');
    expect(existsSync(candidatePath)).toBe(true);
    const replacement = structuredClone(built.template);
    replacement.narrative_id = `${replacement.narrative_id}:replacement`;
    processFixtureProviderResponse({ reportPath, outputRoot, request: built.request, rawResponse: replacement, runId: 'replacement-run' });
    const accepted = readFileSync(candidatePath);
    expect(JSON.parse(accepted.toString()).narrative_id).toMatch(/replacement$/);

    const invalid = structuredClone(built.template);
    invalid.sections[0].claims[0].citations = [];
    expect(() => processFixtureProviderResponse({ reportPath, outputRoot, request: built.request, rawResponse: invalid, runId: 'invalid-run' })).toThrow(/validation failed/i);
    expect(readFileSync(candidatePath)).toEqual(accepted);
    expect(existsSync(join(outputRoot, 'quarantine', 'invalid-run', 'raw-response.json'))).toBe(true);
    expect(existsSync(join(outputRoot, 'quarantine', 'invalid-run', 'validation-errors.json'))).toBe(true);
  }, 30_000);
});
