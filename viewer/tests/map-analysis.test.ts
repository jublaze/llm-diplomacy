import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { boardComparison, orderMarks, reportGeometryIssues, vectorGeometry, visualOutcome } from '../src/map/analysis';
import { canonicalProvinceId, pointForProvince, standardGeometry } from '../src/map/standard-geometry';

const reports: any[] = [game1, game2];
const phases = (report: any) => report.years.flatMap((year: any) => year.phases);

describe('Phase 3 canonical map geometry', () => {
  it('resolves every report location in both generic fixtures without a generated fallback', () => {
    for (const report of reports) expect(reportGeometryIssues(report)).toEqual([]);
    expect(pointForProvince('not_a_province')).toBeUndefined();
    expect(canonicalProvinceId('bul_ec')).toBe('bul');
    expect(canonicalProvinceId('bul_sc')).toBe('bul');
  });

  it('keeps the SVG identifiers and TypeScript geometry in the same canonical coordinate system', () => {
    const svg = readFileSync(`${process.cwd()}/public/maps/standard_clean.svg`, 'utf8');
    expect(svg).toContain('viewBox="0 0 610 560"');
    for (const province of Object.keys(standardGeometry)) {
      if (province.includes('_')) continue;
      expect(svg).toContain(`data-province="${province}"`);
    }
  });

  it('starts and ends movement vectors at canonical report locations', () => {
    const phase = phases(game1).find((item: any) => item.phase_id === '1902_spring_movement')!;
    const mark = orderMarks(game1, phase).find((item) => item.unit_id === 'unit_0023')!;
    const vector = vectorGeometry(mark, 0)!;
    expect(vector.start).toEqual(pointForProvince('bud'));
    expect(vector.end).toEqual(pointForProvince('gal'));
    expect(vector.path).toMatch(new RegExp(`^M ${vector.start.x} ${vector.start.y} Q `));
  });
});

describe('Phase 3 public adjudication model', () => {
  it('derives movement, holds, support, and final outcomes from normalized public phase data', () => {
    const phase = phases(game1).find((item: any) => item.phase_id === '1902_spring_movement')!;
    const marks = orderMarks(game1, phase);

    expect(marks.find((mark) => mark.unit_id === 'unit_0023')).toMatchObject({ kind: 'move', from: 'bud', to: 'gal', outcome: 'moved', renderable: true });
    expect(marks.find((mark) => mark.unit_id === 'unit_0017')).toMatchObject({ kind: 'move', from: 'gal', to: 'bud', outcome: 'dislodged', renderable: true });
    expect(marks.find((mark) => mark.unit_id === 'unit_0016')).toMatchObject({ kind: 'support_move', from: 'rum', to: 'bud', outcome: 'cut' });
    expect(marks.some((mark) => mark.kind === 'hold')).toBe(true);
    expect(marks.every((mark) => (game1 as any).orders[mark.order_id])).toBe(true);
  });

  it('never classifies a failed or bounced movement attempt as a successful capture', () => {
    const phase = phases(game1).find((item: any) => item.phase_id === '1902_spring_movement')!;
    const failed = orderMarks(game1, phase).find((mark) => mark.kind === 'move' && mark.outcome === 'dislodged')!;
    expect(visualOutcome(failed)).toBe('failed');
    expect(vectorGeometry(failed, 0)?.end).toEqual(pointForProvince(failed.to!));
  });

  it('retains non-movement categories and qualifies outcomes the bundle does not establish', () => {
    const convoyPhase = phases(game1).find((item: any) => item.phase_id === '1901_fall_movement')!;
    expect(orderMarks(game1, convoyPhase).find((mark) => mark.kind === 'convoy')).toMatchObject({ from: 'apu', to: 'tun', outcome: 'unconfirmed' });
    const retreatPhase = phases(game1).find((item: any) => item.phase_id === '1902_spring_retreat')!;
    expect(orderMarks(game1, retreatPhase).find((mark) => mark.kind === 'retreat')).toMatchObject({ from: 'gal', to: 'boh' });
    const winter = phases(game1).find((item: any) => item.phase_id === '1902_winter_adjustment')!;
    const marks = orderMarks(game1, winter);
    expect(marks.find((mark) => mark.kind === 'build')).toMatchObject({ power: 'turkey', to: 'smy', outcome: 'built' });
    expect(marks.find((mark) => mark.kind === 'disband')).toMatchObject({ power: 'austria', outcome: 'disbanded' });

    const comparison = boardComparison(game2, phases(game2).find((item: any) => item.phase_id === '1902_spring_movement')!);
    expect(comparison.before_units).toBeGreaterThan(0);
    expect(comparison.after_units).toBeGreaterThan(0);
  });

  it('is generic across valid bundles rather than relying on a Game 1 identity or phase count', () => {
    for (const report of reports) {
      for (const phase of phases(report)) {
        expect(() => orderMarks(report, phase)).not.toThrow();
        expect(reportGeometryIssues(report)).toEqual([]);
      }
    }
  });
});
