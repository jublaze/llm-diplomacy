import game1 from '../fixtures/game_001/report.json';
import { createNarrativeOutline, createNarrativePlan, createTemplateNarrative } from '../src/narrative-generator';
import {
  validateHistorianNarrative,
  validateHistorianNarrativeSchema,
  validateNarrativeOutline,
  validateNarrativePlan,
  validateNarrativePlanSchema,
} from '../src/narrative-model';

const reportSha256 = 'a'.repeat(64);

describe('Phase 4C narrative contracts', () => {
  it('creates deterministic claim-free plans, outlines, and cited template artifacts', () => {
    const firstPlan = createNarrativePlan(game1, reportSha256, 'omniscient_postgame');
    const secondPlan = createNarrativePlan(game1, reportSha256, 'omniscient_postgame');
    const firstOutline = createNarrativeOutline(firstPlan);
    const secondOutline = createNarrativeOutline(secondPlan);
    const firstNarrative = createTemplateNarrative(firstPlan, firstOutline);
    const secondNarrative = createTemplateNarrative(secondPlan, secondOutline);

    expect(firstPlan).toEqual(secondPlan);
    expect(firstOutline).toEqual(secondOutline);
    expect(firstNarrative).toEqual(secondNarrative);
    expect(JSON.stringify(firstPlan)).not.toContain('"text"');
    expect(firstPlan.phases).toHaveLength((game1.years as any[]).flatMap((year) => year.phases).length);
    expect(validateNarrativePlanSchema(firstPlan)).toEqual([]);
    expect(validateNarrativePlan(firstPlan, game1)).toEqual([]);
    expect(validateNarrativeOutline(firstOutline, firstPlan)).toEqual([]);
    expect(validateHistorianNarrativeSchema(firstNarrative)).toEqual([]);
    expect(validateHistorianNarrative(firstNarrative, game1)).toEqual([]);
  });

  it('rejects uncited text, unknown targets, mismatched evidence types, and checksum mismatches', () => {
    const plan = createNarrativePlan(game1, reportSha256, 'omniscient_postgame');
    const artifact = createTemplateNarrative(plan, createNarrativeOutline(plan));

    const uncited = structuredClone(artifact);
    uncited.sections[0].claims[0].citations = [];
    expect(validateHistorianNarrativeSchema(uncited).join('\n')).toMatch(/must NOT have fewer than 1 items/i);

    const unknown = structuredClone(artifact);
    unknown.sections[0].claims[0].citations[0].target.id = 'unknown-target';
    expect(validateHistorianNarrative(unknown, game1).join('\n')).toMatch(/unknown.*target/i);

    const mismatched = structuredClone(artifact);
    mismatched.sections[0].claims[0].citations[0].evidence_type = 'private_record';
    expect(validateHistorianNarrative(mismatched, game1).join('\n')).toMatch(/evidence type/i);

    const checksum = structuredClone(artifact);
    checksum.report_sha256 = 'b'.repeat(64);
    expect(validateHistorianNarrative(checksum, game1, { reportSha256 }).join('\n')).toMatch(/report checksum/i);
  });

  it('rejects private citations from a public narrative', () => {
    const plan = createNarrativePlan(game1, reportSha256, 'omniscient_postgame');
    const artifact = createTemplateNarrative(plan, createNarrativeOutline(plan));
    const privateTarget = plan.phases.flatMap((phase) => phase.allowed_targets).find((target) => target.evidence_type === 'private_record');
    expect(privateTarget).toBeDefined();
    artifact.audience = 'public';
    artifact.sections[0].claims[0].citations = [{ ...privateTarget!, label: 'Private fixture target' }];

    expect(validateHistorianNarrative(artifact, game1).join('\n')).toMatch(/private citation.*public narrative/i);
  });

  it('generates a public plan containing only public evidence', () => {
    const plan = createNarrativePlan(game1, reportSha256, 'public');
    expect(plan.phases.flatMap((phase) => phase.allowed_targets).every((target) => target.visibility === 'public')).toBe(true);
    expect(validateNarrativePlan(plan, game1)).toEqual([]);
    expect(validateHistorianNarrative(createTemplateNarrative(plan, createNarrativeOutline(plan)), game1)).toEqual([]);
  });
});
