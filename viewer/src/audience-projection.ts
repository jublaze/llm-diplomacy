import type { DiplomacyReportBundleV1, JsonValue } from './report-model/types';

export type ReportAudience = 'public' | `player-${string}` | 'omniscient_postgame';
type Report = DiplomacyReportBundleV1;

export const isReportAudience = (value: unknown): value is ReportAudience =>
  value === 'public' || value === 'omniscient_postgame'
  || (typeof value === 'string' && /^player-[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value));

export const audienceAgentId = (audience: ReportAudience): string | null =>
  audience.startsWith('player-') ? audience.slice('player-'.length) : null;

const clone = <T>(value: T): T => structuredClone(value);
const entries = <T>(record: Record<string, T>) => Object.entries(record) as Array<[string, T]>;
const fromEntries = <T>(items: Array<[string, T]>): Record<string, T> => Object.fromEntries(items);

function messageVisible(message: Report['messages'][string], audience: ReportAudience, agentId: string | null): boolean {
  return message.visibility === 'public'
    || (audience !== 'public' && agentId !== null
      && (message.sender_agent_id === agentId || message.recipient_agent_ids.includes(agentId)));
}

function safeGame(report: Report, audience: ReportAudience): Report['game'] {
  const gameNumber = /^game_(\d+)$/.exec(report.source.game_id);
  return {
    title: gameNumber ? `Game ${Number(gameNumber[1])}` : report.source.game_id,
    years_played: clone(report.game.years_played),
    stop_condition: report.game.stop_condition ?? 'completed',
    outcome: clone(report.game.outcome),
    provenance: {
      projection: {
        schema_version: 'audience_projection_v1',
        audience,
      },
    },
  } as Report['game'];
}

/**
 * Produces a standalone audience bundle. Inaccessible objects are removed
 * before the result can be cataloged or sent to the browser.
 */
export function projectReport(input: Report, audience: ReportAudience): Report {
  if (!isReportAudience(audience)) throw new Error(`Unsupported report audience: ${audience}`);
  if (input.source.visibility_mode !== 'omniscient_postgame') throw new Error('Audience projection requires an omniscient_postgame source bundle.');
  if (audience === 'omniscient_postgame') return clone(input);

  const agentId = audienceAgentId(audience);
  if (agentId && !input.roster.some((member) => member.agent_id === agentId)) {
    throw new Error(`Player audience ${audience} does not identify an agent in the report roster.`);
  }

  const messages = fromEntries(entries(input.messages).filter(([, message]) => messageVisible(message, audience, agentId)));
  const records = agentId
    ? fromEntries(entries(input.private_records).filter(([, record]) => record.agent_id === agentId))
    : {};
  const retainedMessageIds = new Set(Object.keys(messages));
  const retainedRecordIds = new Set(Object.keys(records));
  const retainedSources = new Set<string>();
  for (const source of Object.values(input.evidence_index)) if (source.visibility === 'public') retainedSources.add(source.source_id);
  for (const message of Object.values(messages)) retainedSources.add(message.source_ref);
  for (const record of Object.values(records)) if (typeof record.source_ref === 'string') retainedSources.add(record.source_ref);
  const evidence = fromEntries(entries(input.evidence_index).filter(([id]) => retainedSources.has(id)));
  const retainedEvidenceIds = new Set(Object.keys(evidence));
  const retainedOrderIds = new Set(Object.keys(input.orders));

  const years = input.years.map((year) => ({
    ...clone(year),
    phases: year.phases.map((phase) => {
      const sourceRefs = phase.source_refs.filter((id) => retainedEvidenceIds.has(id));
      const orderCount = phase.orders.filter((id) => retainedOrderIds.has(id)).length;
      const messageCount = phase.messages.filter((id) => retainedMessageIds.has(id)).length;
      return {
        ...clone(phase),
        messages: phase.messages.filter((id) => retainedMessageIds.has(id)),
        orders: phase.orders.filter((id) => retainedOrderIds.has(id)),
        private_records: phase.private_records.filter((id) => retainedRecordIds.has(id)),
        narrative: {
          overview: `Audience-projected factual record: ${orderCount} public orders and ${messageCount} visible messages.`,
          theater_sections: [],
          aftermath: '',
          source_refs: sourceRefs,
        },
        source_refs: sourceRefs,
        extensions: {
          ...(typeof phase.extensions?.submitted_order_count === 'number'
            ? { submitted_order_count: phase.extensions.submitted_order_count }
            : {}),
          projection: { schema_version: 'audience_projection_v1', audience },
        },
      };
    }),
  }));

  return {
    schema_version: input.schema_version,
    bundle_id: `diplomacy-report:${input.source.experiment_id}:${input.source.game_id}:${audience}`,
    source: { ...clone(input.source), visibility_mode: audience },
    map: clone(input.map),
    game: safeGame(input, audience),
    roster: clone(input.roster),
    summary: {},
    strategic_arcs: [],
    years,
    messages: clone(messages),
    orders: clone(input.orders),
    private_records: clone(records),
    evidence_index: clone(evidence),
    extensions: {
      projection: {
        schema_version: 'audience_projection_v1',
        audience,
      },
    } as Record<string, JsonValue>,
  };
}

function collectStrings(value: unknown, found = new Set<string>()): Set<string> {
  if (typeof value === 'string') found.add(value);
  else if (Array.isArray(value)) for (const item of value) collectStrings(item, found);
  else if (value && typeof value === 'object') for (const item of Object.values(value)) collectStrings(item, found);
  return found;
}

/** Structural and negative-corpus audit for a projected bundle. */
export function auditProjection(source: Report, projected: Report, audience: ReportAudience): string[] {
  const errors: string[] = [];
  if (audience === 'omniscient_postgame') {
    if (JSON.stringify(source) !== JSON.stringify(projected)) errors.push('omniscient projection changed source bytes');
    return errors;
  }
  if (projected.source.visibility_mode !== audience) errors.push('projected source audience does not match request');
  const agentId = audienceAgentId(audience);
  const projectedStrings = collectStrings(projected);
  const retainedStrings = new Set<string>();
  for (const message of Object.values(projected.messages)) collectStrings(message, retainedStrings);
  for (const record of Object.values(projected.private_records)) collectStrings(record, retainedStrings);

  for (const [id, message] of entries(source.messages)) {
    if (messageVisible(message, audience, agentId)) continue;
    if (id in projected.messages || projectedStrings.has(id)) errors.push(`forbidden message id retained: ${id}`);
    if (projectedStrings.has(message.source_ref)) errors.push(`forbidden message source retained: ${message.source_ref}`);
    if (message.text.length >= 32 && !retainedStrings.has(message.text) && projectedStrings.has(message.text)) errors.push(`forbidden message text retained: ${id}`);
  }
  for (const [id, record] of entries(source.private_records)) {
    if (agentId && record.agent_id === agentId) continue;
    if (id in projected.private_records || projectedStrings.has(id)) errors.push(`forbidden private record id retained: ${id}`);
    if (typeof record.source_ref === 'string' && projectedStrings.has(record.source_ref)) errors.push(`forbidden private record source retained: ${record.source_ref}`);
    const recordStrings = collectStrings(record);
    for (const text of recordStrings) {
      if (text.length >= 64 && !retainedStrings.has(text) && projectedStrings.has(text)) {
        errors.push(`forbidden private record payload retained: ${id}`);
        break;
      }
    }
  }
  for (const [id, evidence] of entries(source.evidence_index)) {
    if (evidence.visibility !== 'omniscient_postgame') continue;
    if (id in projected.evidence_index || projectedStrings.has(id)) errors.push(`forbidden omniscient source retained: ${id}`);
  }
  return errors;
}
