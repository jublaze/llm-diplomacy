// @vitest-environment node
import game1 from '../fixtures/game_001/report.json';
import { auditProjection, projectReport, type ReportAudience } from '../src/audience-projection';
import { validateSchema } from '../src/report-model/validate-schema';
import { validateBundle } from '../src/report-model/validate';
import type { Report } from '../src/viewer-model';

const sourceReport = game1 as unknown as Report;

const eligibleMessage = (message: any, agentId: string) => message.visibility === 'public'
  || message.sender_agent_id === agentId
  || message.recipient_agent_ids.includes(agentId);

const validate = (report: unknown) => [...validateSchema(report), ...validateBundle(report)];

describe('Phase 5A audience projection', () => {
  it('keeps omniscient output unchanged and creates a closed public projection', () => {
    const omniscient = projectReport(sourceReport, 'omniscient_postgame');
    expect(omniscient).toEqual(game1);

    const projected = projectReport(sourceReport, 'public');
    expect(projected.source.visibility_mode).toBe('public');
    expect(projected.bundle_id).not.toBe(game1.bundle_id);
    expect(Object.keys(projected.private_records)).toEqual([]);
    expect(Object.values(projected.messages).every((message: any) => message.visibility === 'public')).toBe(true);
    expect(Object.values(projected.evidence_index).every((source: any) => source.visibility === 'public')).toBe(true);
    expect(projected.summary.editorial).toBeUndefined();
    expect(projected.strategic_arcs).toEqual([]);
    expect(validate(projected)).toEqual([]);
    expect(auditProjection(sourceReport, projected, 'public')).toEqual([]);
  });

  it('retains only a selected player’s messages and records and excludes other-player sentinels', () => {
    const source = structuredClone(game1) as any;
    const retained = Object.values(source.messages).find((message: any) => eligibleMessage(message, 'birsan') && message.visibility === 'private') as any;
    const excluded = Object.values(source.messages).find((message: any) => !eligibleMessage(message, 'birsan')) as any;
    retained.text = 'PHASE5A_RETAINED_PLAYER_SENTINEL';
    excluded.text = 'PHASE5A_EXCLUDED_OTHER_PLAYER_SENTINEL';

    const projected = projectReport(source, 'player-birsan');
    expect(Object.values(projected.messages).every((message: any) => eligibleMessage(message, 'birsan'))).toBe(true);
    expect(Object.values(projected.private_records).every((record: any) => record.agent_id === 'birsan')).toBe(true);
    expect(JSON.stringify(projected)).toContain('PHASE5A_RETAINED_PLAYER_SENTINEL');
    expect(JSON.stringify(projected)).not.toContain('PHASE5A_EXCLUDED_OTHER_PLAYER_SENTINEL');
    expect(validate(projected)).toEqual([]);
    expect(auditProjection(source, projected, 'player-birsan')).toEqual([]);
  });

  it('rejects unknown players and detects forbidden material reintroduced after projection', () => {
    expect(() => projectReport(sourceReport, 'player-unknown')).toThrow(/roster/i);
    const projected = projectReport(sourceReport, 'public') as any;
    const [id, record] = Object.entries(game1.private_records)[0] as [string, any];
    projected.private_records[id] = record;
    projected.evidence_index[record.source_ref] = (game1.evidence_index as any)[record.source_ref];
    expect(auditProjection(sourceReport, projected, 'public').join('\n')).toMatch(/private record|forbidden/i);
  });

  it.each<ReportAudience>(['public', 'player-birsan', 'omniscient_postgame'])('is deterministic for %s', (audience) => {
    expect(JSON.stringify(projectReport(sourceReport, audience))).toBe(JSON.stringify(projectReport(sourceReport, audience)));
  });
});
