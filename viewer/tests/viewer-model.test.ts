import { describe, expect, it } from 'vitest';
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { evidenceHref, findPhase, messageWindow, phaseHref, phaseLabel, phasesForYear, powerSummaries, searchReport, threadGroups } from '../src/viewer-model';

const game1Report: any = game1;
const game2Report: any = game2;

describe('Phase 1 viewer model', () => {
  it('finds each canonical 1902 scene in Game 1', () => {
    expect(findPhase(game1, '1902_spring_movement')?.season).toBe('spring');
    expect(findPhase(game1, '1902_fall_movement')?.season).toBe('fall');
    expect(findPhase(game1, '1902_winter_adjustment')?.season).toBe('winter');
  });

  it('uses preceding and selected negotiation phases for the correspondence window', () => {
    const messages = messageWindow(game1, '1902_spring_movement');
    expect(messages.length).toBeGreaterThan(0);
    expect(new Set(messages.map((message) => message.phase_id))).toContain('1901_winter_negotiation');
    expect(new Set(messages.map((message) => message.phase_id))).toContain('1902_spring_negotiation');
  });

  it('creates stable deep links for a cited phase', () => {
    expect(phaseHref('game_001', '1906_fall_retreat')).toBe('#game=game_001&phase=1906_fall_retreat');
    expect(phaseLabel('1902_winter_adjustment')).toBe('Winter 1902');
  });

  it('loads Game 2 through the same generic helpers', () => {
    expect(findPhase(game2, '1902_spring_movement')?.board_after.units.length).toBeGreaterThan(0);
    expect(messageWindow(game2, '1902_fall_movement').length).toBeGreaterThan(0);
  });

  it('provides three bounded, cited Game 1 1902 scenes without editorializing Game 2', () => {
    for (const id of ['1902_spring_movement', '1902_fall_movement', '1902_winter_adjustment']) {
      const editorial = findPhase(game1Report, id)?.narrative.editorial;
      expect(editorial.sections).toHaveLength(5);
      const words = editorial.sections.flatMap((section: any) => [...section.claims, section.bridge]).map((claim: any) => claim.text).join(' ').trim().split(/\s+/).length;
      expect(words).toBeGreaterThanOrEqual(id.includes('winter') ? 350 : 500);
      expect(words).toBeLessThanOrEqual(id.includes('winter') ? 650 : 850);
      for (const claim of editorial.sections.flatMap((section: any) => [...section.claims, section.bridge])) {
        expect(claim.citations.length).toBeGreaterThan(0);
        for (const citation of claim.citations) expect(game1Report.evidence_index[citation.source_ref]).toBeDefined();
      }
    }
    expect(game1Report.summary.editorial.power_dossiers).toHaveLength(game1Report.roster.length);
    expect(game2Report.summary.editorial).toBeUndefined();
    expect(findPhase(game2Report, '1902_spring_movement')?.narrative.editorial).toBeUndefined();
  });

  it('indexes every phase in a year and keeps generic deep links stable', () => {
    const year1902 = phasesForYear(game1Report, 1902);
    expect(year1902.map((phase: any) => phase.phase_id)).toEqual(['1902_spring_movement', '1902_spring_retreat', '1902_fall_movement', '1902_fall_retreat', '1902_winter_adjustment']);
    expect(evidenceHref('game_001', '1902_spring_movement', { type: 'message', id: '1902_spring_negotiation_helena_0' })).toBe('#game=game_001&phase=1902_spring_movement&evidence=message%3A1902_spring_negotiation_helena_0');
  });

  it('searches bundle data and filters readable conversation threads without per-game UI data', () => {
    expect(searchReport(game1Report, 'helena', 'agent').some((result) => result.kind === 'message')).toBe(true);
    expect(searchReport(game1Report, 'Galicia', 'province').some((result) => result.phase_id === '1902_spring_movement')).toBe(true);
    expect(searchReport(game1Report, 'Ven/Tri', 'source').some((result) => result.kind === 'message')).toBe(true);
    const threads = threadGroups(game1Report, { power: 'italy', phaseId: '1902_spring_negotiation', channel: 'private' });
    expect(threads.some((thread) => thread.thread_id === 'birsan__helena')).toBe(true);
    expect(threads.every((thread) => thread.messages.every((message) => message.phase_id === '1902_spring_negotiation'))).toBe(true);
    const russia = powerSummaries(game1Report).find((power) => power.power === 'russia');
    expect(russia).toMatchObject({ agent_id: 'shaver', final_centers: 12 });
  });
});
