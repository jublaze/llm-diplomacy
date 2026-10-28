import { phases, phaseLabel, type Report } from './viewer-model.ts';
import type {
  AllowedNarrativeTarget,
  HistorianNarrativeV1,
  NarrativeAudience,
  NarrativeClaimType,
  NarrativeOutlineV1,
  NarrativePlanV1,
} from './narrative-model.ts';

export const NARRATIVE_GENERATOR_VERSION = 'deterministic-plan-v1';
export const NARRATIVE_PROMPT_PROFILE = 'historian-v1-review-pending';

const evidenceType = (kind: string): AllowedNarrativeTarget['evidence_type'] | null => {
  if (['board_snapshot', 'orders', 'adjudication', 'manifest'].includes(kind)) return 'public';
  if (kind === 'message') return 'message';
  if (kind === 'private_record') return 'private_record';
  if (kind === 'report') return 'postgame';
  return null;
};

function target(report: Report, targetType: AllowedNarrativeTarget['target']['type'], id: string, sourceRef: string): AllowedNarrativeTarget | null {
  const source = report.evidence_index?.[sourceRef];
  const type = source && evidenceType(source.kind);
  if (!source || !type) return null;
  return { source_ref: sourceRef, evidence_type: type, visibility: source.visibility, target: { type: targetType, id } };
}

const targetKey = (item: AllowedNarrativeTarget) => `${item.target.type}:${item.target.id}:${item.source_ref}`;

export function createNarrativePlan(report: Report, reportSha256: string, audience: NarrativeAudience): NarrativePlanV1 {
  const plannedPhases = phases(report).map((phase: any) => {
    const targets: AllowedNarrativeTarget[] = [];
    for (const sourceRef of phase.source_refs ?? []) {
      const item = target(report, 'source', sourceRef, sourceRef);
      if (item) targets.push(item);
    }
    for (const id of phase.messages ?? []) {
      const item = target(report, 'message', id, report.messages[id]?.source_ref);
      if (item) targets.push(item);
    }
    for (const id of phase.orders ?? []) {
      const item = target(report, 'order', id, report.orders[id]?.source_ref);
      if (item) targets.push(item);
    }
    for (const id of phase.private_records ?? []) {
      const item = target(report, 'record', id, report.private_records[id]?.source_ref);
      if (item) targets.push(item);
    }
    const unique = [...new Map(targets.map((item) => [targetKey(item), item])).values()]
      .filter((item) => audience !== 'public' || item.visibility === 'public')
      .sort((left, right) => targetKey(left).localeCompare(targetKey(right)));
    return {
      phase_id: phase.phase_id,
      year: phase.year,
      season: phase.season,
      kind: phase.kind,
      source_ids: [...new Set(unique.map((item) => item.source_ref))].sort(),
      allowed_targets: unique,
      observations: {
        active_units_before: phase.board_before.units.filter((unit: any) => unit.status === 'active').length,
        active_units_after: phase.board_after.units.filter((unit: any) => unit.status === 'active').length,
        supply_centers_before: Object.values(phase.board_before.supply_ownership ?? {}).filter(Boolean).length,
        supply_centers_after: Object.values(phase.board_after.supply_ownership ?? {}).filter(Boolean).length,
        message_count: phase.messages.length,
        order_count: phase.orders.length,
        private_record_count: phase.private_records.length,
      },
    };
  });
  return {
    schema_version: 'narrative_plan_v1',
    plan_id: `${report.bundle_id}:narrative-plan:${audience}:${reportSha256.slice(0, 12)}`,
    report_bundle_id: report.bundle_id,
    report_sha256: reportSha256,
    audience,
    generator: { generator_version: NARRATIVE_GENERATOR_VERSION, prompt_profile_version: NARRATIVE_PROMPT_PROFILE },
    phases: plannedPhases,
  };
}

function allowedClaimTypes(targets: AllowedNarrativeTarget[]): NarrativeClaimType[] {
  const types: NarrativeClaimType[] = [];
  if (targets.some((item) => item.evidence_type === 'public')) types.push('public_fact');
  if (targets.some((item) => ['message', 'private_record'].includes(item.evidence_type))) types.push('attributed_private_assessment');
  if (targets.some((item) => item.evidence_type === 'postgame')) types.push('postgame_interpretation');
  return types;
}

export function createNarrativeOutline(plan: NarrativePlanV1): NarrativeOutlineV1 {
  return {
    schema_version: 'narrative_outline_v1',
    outline_id: `${plan.plan_id}:outline:v1`,
    plan_id: plan.plan_id,
    report_bundle_id: plan.report_bundle_id,
    report_sha256: plan.report_sha256,
    audience: plan.audience,
    sections: plan.phases.filter((phase) => phase.allowed_targets.length > 0).map((phase) => {
      const claimTypes = allowedClaimTypes(phase.allowed_targets);
      const preferred = phase.allowed_targets.find((item) => item.evidence_type === 'public') ?? phase.allowed_targets[0];
      return {
        section_id: `phase:${phase.phase_id}`,
        phase_id: phase.phase_id,
        heading: `${phaseLabel(phase.phase_id)} — factual record`,
        allowed_claim_types: claimTypes,
        minimum_citations: [preferred],
      };
    }),
  };
}

export function createTemplateNarrative(plan: NarrativePlanV1, outline: NarrativeOutlineV1): HistorianNarrativeV1 {
  const byPhase = new Map(plan.phases.map((phase) => [phase.phase_id, phase]));
  return {
    schema_version: 'historian_narrative_v1',
    narrative_id: `${outline.outline_id}:template-candidate:v1`,
    plan_id: plan.plan_id,
    report_bundle_id: plan.report_bundle_id,
    report_sha256: plan.report_sha256,
    audience: plan.audience,
    generator: { adapter: 'deterministic_template', adapter_version: 'v1', prompt_profile_version: plan.generator.prompt_profile_version },
    sections: outline.sections.map((section) => {
      const phase = byPhase.get(section.phase_id)!;
      const citation = section.minimum_citations[0];
      const claimType: NarrativeClaimType = citation.evidence_type === 'public'
        ? 'public_fact'
        : ['message', 'private_record'].includes(citation.evidence_type) ? 'attributed_private_assessment' : 'postgame_interpretation';
      const observation = phase.observations;
      return {
        section_id: section.section_id,
        phase_id: section.phase_id,
        heading: section.heading,
        claim_type: claimType,
        claims: [{
          text: `${phaseLabel(section.phase_id)} records ${observation.order_count} public orders, ${observation.message_count} preserved messages, and an active-unit count changing from ${observation.active_units_before} to ${observation.active_units_after}.`,
          citations: [{ ...citation, label: `${phaseLabel(section.phase_id)} source record` }],
        }],
      };
    }),
  };
}

export type NarrativeProviderRequestV1 = {
  schema_version: 'narrative_provider_request_v1';
  request_id: string;
  report_bundle_id: string;
  report_sha256: string;
  audience: NarrativeAudience;
  model: string;
  model_version: string;
  temperature: number;
  seed: number;
  prompt_profile_version: string;
  plan: NarrativePlanV1;
  outline: NarrativeOutlineV1;
};

export function createSavedProviderRequest(plan: NarrativePlanV1, outline: NarrativeOutlineV1): NarrativeProviderRequestV1 {
  return {
    schema_version: 'narrative_provider_request_v1',
    request_id: `${outline.outline_id}:fixture-request:v1`,
    report_bundle_id: plan.report_bundle_id,
    report_sha256: plan.report_sha256,
    audience: plan.audience,
    model: 'fixture-template',
    model_version: 'v1',
    temperature: 0,
    seed: 0,
    prompt_profile_version: plan.generator.prompt_profile_version,
    plan,
    outline,
  };
}

export interface NarrativeProviderAdapter {
  generate(request: NarrativeProviderRequestV1): Promise<unknown>;
}

export class FixtureNarrativeProviderAdapter implements NarrativeProviderAdapter {
  private readonly response: unknown;
  constructor(response: unknown) { this.response = response; }
  async generate(_request: NarrativeProviderRequestV1): Promise<unknown> { return structuredClone(this.response); }
}
