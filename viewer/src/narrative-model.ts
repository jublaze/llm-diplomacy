import Ajv2020 from 'ajv/dist/2020.js';
import planSchema from '../contracts/narrative_plan_v1.schema.json' with { type: 'json' };
import narrativeSchema from '../contracts/historian_narrative_v1.schema.json' with { type: 'json' };
import type { EvidenceTarget, Report } from './viewer-model.ts';

export type NarrativeAudience = 'public' | 'omniscient_postgame';
export type NarrativeEvidenceType = 'public' | 'message' | 'private_record' | 'postgame';
export type NarrativeClaimType = 'public_fact' | 'attributed_private_assessment' | 'postgame_interpretation';

export type AllowedNarrativeTarget = {
  source_ref: string;
  evidence_type: NarrativeEvidenceType;
  visibility: 'public' | 'private' | 'omniscient_postgame';
  target: EvidenceTarget;
};

export type NarrativeObservations = {
  active_units_before: number;
  active_units_after: number;
  supply_centers_before: number;
  supply_centers_after: number;
  message_count: number;
  order_count: number;
  private_record_count: number;
};

export type NarrativePlanV1 = {
  schema_version: 'narrative_plan_v1';
  plan_id: string;
  report_bundle_id: string;
  report_sha256: string;
  audience: NarrativeAudience;
  generator: { generator_version: string; prompt_profile_version: string };
  phases: Array<{
    phase_id: string;
    year: number;
    season: string;
    kind: string;
    source_ids: string[];
    allowed_targets: AllowedNarrativeTarget[];
    observations: NarrativeObservations;
  }>;
};

export type NarrativeOutlineV1 = {
  schema_version: 'narrative_outline_v1';
  outline_id: string;
  plan_id: string;
  report_bundle_id: string;
  report_sha256: string;
  audience: NarrativeAudience;
  sections: Array<{
    section_id: string;
    phase_id: string;
    heading: string;
    allowed_claim_types: NarrativeClaimType[];
    minimum_citations: AllowedNarrativeTarget[];
  }>;
};

export type NarrativeCitation = AllowedNarrativeTarget & { label: string };
export type HistorianNarrativeV1 = {
  schema_version: 'historian_narrative_v1';
  narrative_id: string;
  plan_id: string;
  report_bundle_id: string;
  report_sha256: string;
  audience: NarrativeAudience;
  generator: { adapter: string; adapter_version: string; prompt_profile_version: string };
  sections: Array<{
    section_id: string;
    phase_id: string;
    heading: string;
    claim_type: NarrativeClaimType;
    claims: Array<{ text: string; citations: NarrativeCitation[] }>;
  }>;
};

const ajv = new Ajv2020({ allErrors: true, strict: false });
const checkPlan = ajv.compile(planSchema);
const checkNarrative = ajv.compile(narrativeSchema);
const schemaErrors = (errors: typeof checkPlan.errors) => (errors ?? []).map((error) => `${error.instancePath || '/'}: ${error.message}`);
const object = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);

export function validateNarrativePlanSchema(value: unknown): string[] {
  return checkPlan(value) ? [] : schemaErrors(checkPlan.errors);
}

export function validateHistorianNarrativeSchema(value: unknown): string[] {
  return checkNarrative(value) ? [] : schemaErrors(checkNarrative.errors);
}

const expectedEvidenceType = (kind: string): NarrativeEvidenceType | null => {
  if (['board_snapshot', 'orders', 'adjudication', 'manifest'].includes(kind)) return 'public';
  if (kind === 'message') return 'message';
  if (kind === 'private_record') return 'private_record';
  if (kind === 'report') return 'postgame';
  return null;
};

function resolveTarget(report: Report, item: AllowedNarrativeTarget): { source: any; error?: string } {
  const source = report.evidence_index?.[item.source_ref];
  if (!source) return { source: null, error: `unknown source ${item.source_ref}` };
  let targetSource: string | undefined;
  if (item.target.type === 'source') targetSource = report.evidence_index?.[item.target.id] ? item.target.id : undefined;
  if (item.target.type === 'message') targetSource = report.messages?.[item.target.id]?.source_ref;
  if (item.target.type === 'record') targetSource = report.private_records?.[item.target.id]?.source_ref;
  if (item.target.type === 'order') targetSource = report.orders?.[item.target.id]?.source_ref;
  if (!targetSource) return { source, error: `unknown ${item.target.type} target ${item.target.id}` };
  if (targetSource !== item.source_ref) return { source, error: `target ${item.target.id} does not resolve to source ${item.source_ref}` };
  const expected = expectedEvidenceType(source.kind);
  if (expected !== item.evidence_type) return { source, error: `evidence type ${item.evidence_type} does not match ${source.kind}` };
  if (source.visibility !== item.visibility) return { source, error: `citation visibility ${item.visibility} does not match source visibility ${source.visibility}` };
  return { source };
}

export function validateNarrativePlan(value: unknown, report: Report, options: { reportSha256?: string } = {}): string[] {
  const schema = validateNarrativePlanSchema(value);
  if (schema.length) return schema;
  const plan = value as NarrativePlanV1;
  const errors: string[] = [];
  if (plan.report_bundle_id !== report.bundle_id) errors.push('/report_bundle_id: does not match factual report');
  if (options.reportSha256 && plan.report_sha256 !== options.reportSha256) errors.push('/report_sha256: report checksum mismatch');
  const phaseIds = new Set(report.years.flatMap((year: any) => year.phases.map((phase: any) => phase.phase_id)));
  const factualPhases = new Map<string, any>((report.years.flatMap((year: any) => year.phases) as any[]).map((phase: any) => [phase.phase_id, phase]));
  const seen = new Set<string>();
  for (const [phaseIndex, phase] of plan.phases.entries()) {
    if (!phaseIds.has(phase.phase_id)) errors.push(`/phases/${phaseIndex}/phase_id: unknown factual phase`);
    if (seen.has(phase.phase_id)) errors.push(`/phases/${phaseIndex}/phase_id: duplicate phase`);
    seen.add(phase.phase_id);
    const factual = factualPhases.get(phase.phase_id);
    if (factual) {
      if (phase.year !== factual.year || phase.season !== factual.season || phase.kind !== factual.kind) errors.push(`/phases/${phaseIndex}: chronology metadata does not match factual phase`);
      const expected: NarrativeObservations = {
        active_units_before: factual.board_before.units.filter((unit: any) => unit.status === 'active').length,
        active_units_after: factual.board_after.units.filter((unit: any) => unit.status === 'active').length,
        supply_centers_before: Object.values(factual.board_before.supply_ownership ?? {}).filter(Boolean).length,
        supply_centers_after: Object.values(factual.board_after.supply_ownership ?? {}).filter(Boolean).length,
        message_count: factual.messages.length,
        order_count: factual.orders.length,
        private_record_count: factual.private_records.length,
      };
      for (const [field, count] of Object.entries(expected)) if (phase.observations[field as keyof NarrativeObservations] !== count) errors.push(`/phases/${phaseIndex}/observations/${field}: does not match factual phase`);
    }
    const targetSources = [...new Set(phase.allowed_targets.map((item) => item.source_ref))].sort();
    if (JSON.stringify(phase.source_ids) !== JSON.stringify(targetSources)) errors.push(`/phases/${phaseIndex}/source_ids: must equal allowed target source IDs in sorted order`);
    for (const [targetIndex, target] of phase.allowed_targets.entries()) {
      const resolved = resolveTarget(report, target);
      if (resolved.error) errors.push(`/phases/${phaseIndex}/allowed_targets/${targetIndex}: ${resolved.error}`);
      if (plan.audience === 'public' && resolved.source?.visibility !== 'public') errors.push(`/phases/${phaseIndex}/allowed_targets/${targetIndex}: private citation is not allowed in a public narrative plan`);
    }
  }
  return errors;
}

const targetKey = (target: AllowedNarrativeTarget) => `${target.target.type}:${target.target.id}:${target.source_ref}:${target.evidence_type}`;

export function validateNarrativeOutline(value: unknown, plan: NarrativePlanV1): string[] {
  if (!object(value) || value.schema_version !== 'narrative_outline_v1' || !Array.isArray(value.sections)) return ['/: invalid narrative_outline_v1'];
  const outline = value as NarrativeOutlineV1;
  const errors: string[] = [];
  if (outline.plan_id !== plan.plan_id || outline.report_bundle_id !== plan.report_bundle_id || outline.report_sha256 !== plan.report_sha256 || outline.audience !== plan.audience) errors.push('/: outline binding does not match plan');
  const byPhase = new Map(plan.phases.map((phase) => [phase.phase_id, phase]));
  for (const [index, section] of outline.sections.entries()) {
    const phase = byPhase.get(section.phase_id);
    if (!phase) { errors.push(`/sections/${index}/phase_id: unknown plan phase`); continue; }
    const allowed = new Set(phase.allowed_targets.map(targetKey));
    if (!section.minimum_citations.length) errors.push(`/sections/${index}/minimum_citations: needs at least one target`);
    for (const target of section.minimum_citations) if (!allowed.has(targetKey(target))) errors.push(`/sections/${index}/minimum_citations: target is not allowed by the plan`);
  }
  return errors;
}

export function validateHistorianNarrative(value: unknown, report: Report, options: { reportSha256?: string; audience?: NarrativeAudience; plan?: NarrativePlanV1 } = {}): string[] {
  const schema = validateHistorianNarrativeSchema(value);
  if (schema.length) return schema;
  const narrative = value as HistorianNarrativeV1;
  const errors: string[] = [];
  if (narrative.report_bundle_id !== report.bundle_id) errors.push('/report_bundle_id: does not match factual report');
  if (options.reportSha256 && narrative.report_sha256 !== options.reportSha256) errors.push('/report_sha256: report checksum mismatch');
  if (options.audience && narrative.audience !== options.audience) errors.push('/audience: does not match catalog audience');
  if (options.plan && narrative.plan_id !== options.plan.plan_id) errors.push('/plan_id: does not match narrative plan');
  const phaseIds = new Set(report.years.flatMap((year: any) => year.phases.map((phase: any) => phase.phase_id)));
  const planTargets = options.plan ? new Map(options.plan.phases.map((phase) => [phase.phase_id, new Set(phase.allowed_targets.map(targetKey))])) : null;
  for (const [sectionIndex, section] of narrative.sections.entries()) {
    if (!phaseIds.has(section.phase_id)) errors.push(`/sections/${sectionIndex}/phase_id: unknown factual phase`);
    for (const [claimIndex, claim] of section.claims.entries()) {
      if (!claim.text.trim()) errors.push(`/sections/${sectionIndex}/claims/${claimIndex}/text: claim text is empty`);
      for (const [citationIndex, citation] of claim.citations.entries()) {
        const path = `/sections/${sectionIndex}/claims/${claimIndex}/citations/${citationIndex}`;
        const resolved = resolveTarget(report, citation);
        if (resolved.error) errors.push(`${path}: ${resolved.error}`);
        if (narrative.audience === 'public' && resolved.source?.visibility !== 'public') errors.push(`${path}: private citation is not allowed in a public narrative`);
        if (planTargets && !planTargets.get(section.phase_id)?.has(targetKey(citation))) errors.push(`${path}: citation is not allowed by the narrative plan`);
        if (section.claim_type === 'public_fact' && citation.evidence_type !== 'public') errors.push(`${path}: public fact requires public evidence type`);
        if (section.claim_type === 'attributed_private_assessment' && !['message', 'private_record'].includes(citation.evidence_type)) errors.push(`${path}: attributed private assessment requires message or private record evidence`);
      }
    }
  }
  return errors;
}
