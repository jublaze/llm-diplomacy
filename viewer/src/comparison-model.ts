import type { ReportCatalogEntry } from './report-loader';
import type { Report } from './viewer-model';

export type ComparisonIssueKind = 'selection' | 'audience' | 'schema' | 'map' | 'missing';
export type ComparisonIssue = { kind: ComparisonIssueKind; message: string };
export type ComparisonProvenance = {
  report_id: string;
  bundle_id: string;
  label: string;
  href: string;
  phase_id?: string;
  source_ref?: string;
};
export type ComparisonCell = { display: string; status: 'available' | 'missing'; provenance: ComparisonProvenance[] };
export type ComparisonMetricRow = { key: string; label: string; cells: ComparisonCell[] };
export type ComparisonPowerRow = { power: string; cells: ComparisonCell[] };
export type ComparisonInput = { entry: ReportCatalogEntry; report: Report };
export type ComparisonModel = {
  report_ids: string[];
  labels: string[];
  compatible: boolean;
  issues: ComparisonIssue[];
  metrics: ComparisonMetricRow[];
  powers: ComparisonPowerRow[];
};

const phases = (report: Report) => report.years.flatMap((year: any) => year.phases);
const activeUnits = (board: any) => board.units.filter((unit: any) => unit.status === 'active').length;
const centerCounts = (board: any) => Object.values(board.supply_ownership).reduce<Record<string, number>>((counts, owner) => {
  if (typeof owner === 'string' && owner) counts[owner] = (counts[owner] ?? 0) + 1;
  return counts;
}, {});
const major = (schema: string) => /_v(\d+)$/.exec(schema)?.[1] ?? schema;
const number = (value: number) => new Intl.NumberFormat('en-US').format(value);

function href(entry: ReportCatalogEntry, options: { phase?: string; evidence?: { type: 'source' | 'message' | 'order'; id: string } } = {}) {
  const params = new URLSearchParams({ game: entry.id });
  if (options.phase) params.set('phase', options.phase);
  else params.set('view', 'home');
  if (options.evidence) params.set('evidence', `${options.evidence.type}:${options.evidence.id}`);
  return `#${params.toString()}`;
}

function provenance(input: ComparisonInput, label: string, options: { phase?: string; sourceRef?: string; evidence?: { type: 'source' | 'message' | 'order'; id: string } } = {}): ComparisonProvenance {
  return { report_id: input.entry.id, bundle_id: input.report.bundle_id, label, href: href(input.entry, options), ...(options.phase ? { phase_id: options.phase } : {}), ...(options.sourceRef ? { source_ref: options.sourceRef } : {}) };
}

function available(display: string, provenanceLinks: ComparisonProvenance[]): ComparisonCell {
  return { display, status: 'available', provenance: provenanceLinks };
}

function missing(input: ComparisonInput, metric: string, issues: ComparisonIssue[]): ComparisonCell {
  issues.push({ kind: 'missing', message: `${input.entry.label}: ${metric} is not recorded for this audience bundle.` });
  return { display: 'Not recorded', status: 'missing', provenance: [provenance(input, 'Open bundle context')] };
}

export function buildComparison(inputs: ComparisonInput[]): ComparisonModel {
  const issues: ComparisonIssue[] = [];
  if (inputs.length < 2) issues.push({ kind: 'selection', message: 'Select at least two reports to compare.' });
  const audiences = new Set(inputs.map(({ entry }) => entry.audience));
  const schemas = new Set(inputs.map(({ report }) => major(report.schema_version)));
  const maps = new Set(inputs.map(({ report }) => report.map.map_id));
  if (audiences.size > 1) issues.push({ kind: 'audience', message: 'Selected reports use different audience projections and cannot be combined safely.' });
  if (schemas.size > 1) issues.push({ kind: 'schema', message: 'Selected reports use incompatible report schema majors.' });
  if (maps.size > 1) issues.push({ kind: 'map', message: 'Selected reports use incompatible maps; board-derived values are shown separately, not combined.' });

  const facts = inputs.map((input) => {
    const reportPhases = phases(input.report);
    const first = reportPhases[0];
    const last = reportPhases.at(-1);
    const publicMessages = Object.values(input.report.messages).filter((message: any) => message.visibility === 'public');
    const firstMessage = publicMessages[0] as any;
    const orders = Object.entries(input.report.orders) as Array<[string, any]>;
    const firstOrder = orders[0];
    const submittedValues = reportPhases.map((phase: any) => phase.extensions?.submitted_order_count).filter((value: unknown) => typeof value === 'number');
    const costByAgent = (input.report.game as any).cost_and_call_metadata?.by_agent;
    const costValues = costByAgent && typeof costByAgent === 'object' ? Object.values(costByAgent) as any[] : null;
    const reportSource = Object.values(input.report.evidence_index).find((source: any) => source.kind === 'report') as any;
    return {
      input, reportPhases, first, last, publicMessages, firstMessage, orders, firstOrder,
      submitted: submittedValues.length === reportPhases.length ? submittedValues.reduce((sum: number, value: any) => sum + value, 0) : null,
      effective: orders.filter(([, order]) => order.effective_status !== 'no_effective_order').length,
      calls: costValues?.every((item) => typeof item.calls === 'number') ? costValues.reduce((sum, item) => sum + item.calls, 0) : null,
      cost: costValues?.every((item) => typeof item.estimated_cost === 'number') ? costValues.reduce((sum, item) => sum + item.estimated_cost, 0) : null,
      reportSource,
    };
  });

  const metric = (key: string, label: string, cells: (fact: typeof facts[number]) => ComparisonCell): ComparisonMetricRow => ({ key, label, cells: facts.map(cells) });
  const metrics: ComparisonMetricRow[] = [
    metric('experiment_id', 'Experiment ID', ({ input }) => available(input.report.source.experiment_id, [provenance(input, 'Open bundle context')])),
    metric('game_id', 'Game ID', ({ input }) => available(input.report.source.game_id, [provenance(input, 'Open game context')])),
    metric('schema_version', 'Report schema', ({ input }) => available(input.report.schema_version, [provenance(input, 'Open bundle context')])),
    metric('map_version', 'Map / geometry', ({ input }) => available(`${input.report.map.map_id} · ${input.report.map.geometry_version}`, [provenance(input, 'Open map context')])),
    metric('years_played', 'Years played', ({ input, first, last }) => available((input.report.game.years_played as number[]).join('–'), [provenance(input, 'Open first phase', { phase: first?.phase_id }), provenance(input, 'Open final phase', { phase: last?.phase_id })])),
    metric('termination', 'Termination', ({ input, last }) => available(`${(input.report.game.outcome as any).termination.kind} · ${(input.report.game.outcome as any).termination.label}`, [provenance(input, 'Open final phase', { phase: last?.phase_id })])),
    metric('leaders', 'Recorded final leaders', ({ input, last }) => available((input.report.game.outcome as any).leaders.map((leader: any) => `${leader.power} / ${leader.agent_id}: ${leader.supply_centers}`).join('; ') || 'No recorded leader', [provenance(input, 'Open final phase', { phase: last?.phase_id })])),
    metric('phase_count', 'Recorded phases', ({ input, reportPhases, first }) => available(number(reportPhases.length), [provenance(input, 'Open phase context', { phase: first?.phase_id })])),
    metric('active_units', 'Active units, start → final', ({ input, first, last }) => first && last ? available(`${activeUnits(first.board_before)} → ${activeUnits(last.board_after)}`, [provenance(input, 'Open first phase', { phase: first.phase_id }), provenance(input, 'Open final phase', { phase: last.phase_id })]) : missing(input, 'active-unit counts', issues)),
    metric('public_messages', 'Public messages', ({ input, publicMessages, firstMessage }) => available(number(publicMessages.length), firstMessage ? [provenance(input, 'Open source context', { phase: firstMessage.phase_id, sourceRef: firstMessage.source_ref, evidence: { type: 'message', id: firstMessage.message_id } })] : [provenance(input, 'Open bundle context')])),
    metric('submitted_orders', 'Submitted orders', ({ input, submitted, firstOrder }) => submitted === null ? missing(input, 'submitted-order count', issues) : available(number(submitted), firstOrder ? [provenance(input, 'Open source context', { sourceRef: firstOrder[1].source_ref, evidence: { type: 'order', id: firstOrder[0] } })] : [provenance(input, 'Open bundle context')])),
    metric('effective_orders', 'Effective/defaulted orders', ({ input, effective, firstOrder }) => available(number(effective), firstOrder ? [provenance(input, 'Open source context', { sourceRef: firstOrder[1].source_ref, evidence: { type: 'order', id: firstOrder[0] } })] : [provenance(input, 'Open bundle context')])),
    metric('provider_calls', 'Recorded provider calls', ({ input, calls, reportSource }) => calls === null ? missing(input, 'provider-call metadata', issues) : available(number(calls), [provenance(input, 'Open source context', reportSource ? { sourceRef: reportSource.source_id, evidence: { type: 'source', id: reportSource.source_id } } : {})])),
    metric('estimated_cost', 'Recorded estimated cost (USD)', ({ input, cost, reportSource }) => cost === null ? missing(input, 'estimated-cost metadata', issues) : available(`$${cost.toFixed(2)}`, [provenance(input, 'Open source context', reportSource ? { sourceRef: reportSource.source_id, evidence: { type: 'source', id: reportSource.source_id } } : {})])),
  ];

  const powers = [...new Set(inputs.flatMap(({ report }) => report.roster.map((member: any) => member.power as string)))].sort();
  const powerRows: ComparisonPowerRow[] = powers.map((power) => ({
    power,
    cells: facts.map(({ input, first, last }) => {
      if (!first || !last) return missing(input, `${power} center counts`, issues);
      const start = centerCounts(first.board_before)[power] ?? 0;
      const finish = centerCounts(last.board_after)[power] ?? 0;
      const delta = finish - start;
      return available(`${start} → ${finish} (${delta >= 0 ? '+' : ''}${delta})`, [provenance(input, 'Open first phase', { phase: first.phase_id }), provenance(input, 'Open final phase', { phase: last.phase_id })]);
    }),
  }));

  return {
    report_ids: inputs.map(({ entry }) => entry.id),
    labels: inputs.map(({ entry }) => entry.label),
    compatible: !issues.some((issue) => issue.kind !== 'missing'),
    issues,
    metrics,
    powers: powerRows,
  };
}
