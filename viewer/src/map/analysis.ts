import { pointForProvince, type MapPoint } from './standard-geometry';

export type OrderKind = 'move' | 'hold' | 'support_move' | 'support_hold' | 'convoy' | 'retreat' | 'build' | 'disband' | 'unsupported';
export type VisualOutcome = 'successful' | 'failed' | 'qualified' | 'neutral';
export type OrderMark = {
  order_id: string;
  unit_id: string;
  power: string;
  kind: OrderKind;
  from?: string;
  to?: string;
  outcome: string;
  notation: string;
  renderable: boolean;
  map_note?: string;
};

export type BoardComparison = {
  before_units: number;
  after_units: number;
  moves: number;
  dislodgements: number;
  adjustments: number;
  center_changes: number;
  standoffs: number;
};

export type MapVector = { path: string; start: MapPoint; end: MapPoint; label: { x: number; y: number } };
export type GeometryIssue = { location: string; context: string };

const activeUnits = (board: any) => (board?.units ?? []).filter((unit: any) => unit.status !== 'destroyed');
const outcomeMap = (items: any[] = []) => new Map(items.map((item) => [item.unit_id, item.result ?? item.status ?? 'unconfirmed']));
const orderKind: Record<string, OrderKind> = {
  MOVE: 'move', HOLD: 'hold', SUPPORT_MOVE: 'support_move', SUPPORT_HOLD: 'support_hold', CONVOY: 'convoy', RETREAT: 'retreat', BUILD: 'build', DISBAND: 'disband', NO_EFFECTIVE_ORDER: 'unsupported'
};

const routeKinds = new Set<OrderKind>(['move', 'support_move', 'support_hold', 'convoy', 'retreat']);
const successfulOutcomes: Record<OrderKind, Set<string>> = {
  move: new Set(['moved']), hold: new Set(['held']), support_move: new Set(['effective']), support_hold: new Set(['effective']), convoy: new Set(['effective']), retreat: new Set(['retreated']), build: new Set(['built']), disband: new Set(['disbanded']), unsupported: new Set()
};
const failedOutcomes = new Set(['bounced', 'failed', 'held', 'dislodged', 'cut', 'destroyed', 'illegal', 'void', 'not_built', 'not_disbanded']);

export function visualOutcome(mark: OrderMark): VisualOutcome {
  if (mark.kind === 'unsupported') return 'neutral';
  if (successfulOutcomes[mark.kind].has(mark.outcome)) return 'successful';
  if (failedOutcomes.has(mark.outcome)) return 'failed';
  return 'qualified';
}

function mapped(mark: Pick<OrderMark, 'kind' | 'from' | 'to'>): { renderable: boolean; map_note?: string } {
  if (mark.kind === 'unsupported') return { renderable: false, map_note: 'Order type has no confident graphical treatment.' };
  const required = routeKinds.has(mark.kind) ? [mark.from, mark.to] : [mark.from ?? mark.to];
  const missing = required.find((location) => !location || !pointForProvince(location));
  return missing ? { renderable: false, map_note: missing ? `Location ${missing} is not mapped by this geometry.` : 'Required location is absent.' } : { renderable: true };
}

/** Converts source-backed public orders and adjudication into generic map/text records. */
export function orderMarks(report: any, phase: any): OrderMark[] {
  const movement = outcomeMap(phase.adjudication?.movement_outcomes);
  const support = outcomeMap(phase.adjudication?.support_outcomes);
  const convoy = outcomeMap(phase.adjudication?.convoy_outcomes);
  const retreat = outcomeMap(phase.adjudication?.retreat_outcomes);
  const adjustments: any[] = phase.adjudication?.adjustment_outcomes ?? [];
  const beforeByUnit = new Map<string, any>((phase.board_before?.units ?? []).map((unit: any) => [unit.unit_id, unit]));

  return (phase.orders ?? []).flatMap((orderId: string) => {
    const order = report.orders?.[orderId];
    if (!order) return [];
    const kind = orderKind[order.order_type] ?? 'unsupported';
    const payload = order.payload ?? {};
    let from: string | undefined;
    let to: string | undefined;
    let outcome = 'unconfirmed';
    if (kind === 'move') { from = payload.from; to = payload.to; outcome = movement.get(order.unit_id) ?? 'unconfirmed'; }
    if (kind === 'hold') { from = payload.at ?? beforeByUnit.get(order.unit_id)?.province; outcome = movement.get(order.unit_id) ?? 'unconfirmed'; }
    if (kind === 'support_move') { from = payload.from; to = payload.supported_to; outcome = support.get(order.unit_id) ?? 'unconfirmed'; }
    if (kind === 'support_hold') { from = payload.from; to = payload.target; outcome = support.get(order.unit_id) ?? 'unconfirmed'; }
    if (kind === 'convoy') { from = payload.army_from ?? payload.from ?? payload.at; to = payload.army_to ?? payload.to ?? payload.convoy_to; outcome = convoy.get(order.unit_id) ?? 'unconfirmed'; }
    if (kind === 'retreat') { from = payload.from; to = payload.to; outcome = retreat.get(order.unit_id) ?? 'unconfirmed'; }
    if (kind === 'build') {
      to = payload.province;
      outcome = adjustments.find((item) => item.order_id === orderId || item.unit_id === order.unit_id || (item.power === order.power && item.province === to && item.result === 'built'))?.result ?? 'unconfirmed';
    }
    if (kind === 'disband') {
      const unitId = payload.unit_ref ?? order.unit_id;
      from = beforeByUnit.get(unitId)?.province;
      outcome = adjustments.find((item) => item.order_id === orderId || item.unit_id === unitId)?.result ?? 'unconfirmed';
    }
    const base = { order_id: orderId, unit_id: order.unit_id ?? '', power: order.power ?? 'unknown', kind, from, to, outcome, notation: order.notation ?? order.order_type ?? orderId };
    return [{ ...base, ...mapped(base) }];
  });
}

/** Quadratic route whose exact endpoints are the canonical map anchors. */
export function vectorGeometry(mark: OrderMark, routeOffset = 0): MapVector | undefined {
  if (!mark.renderable || !routeKinds.has(mark.kind) || !mark.from || !mark.to) return undefined;
  const start = pointForProvince(mark.from); const end = pointForProvince(mark.to);
  if (!start || !end) return undefined;
  const dx = end.x - start.x; const dy = end.y - start.y; const length = Math.hypot(dx, dy) || 1;
  const offset = routeOffset * 11;
  const label = { x: (start.x + end.x) / 2 - (dy / length) * offset, y: (start.y + end.y) / 2 + (dx / length) * offset };
  return { start, end, label, path: `M ${start.x} ${start.y} Q ${label.x} ${label.y} ${end.x} ${end.y}` };
}

/** Reports only explicit bundle locations; it never creates replacement coordinates. */
export function reportGeometryIssues(report: any): GeometryIssue[] {
  const issues = new Map<string, GeometryIssue>();
  const check = (location: unknown, context: string) => {
    if (typeof location !== 'string' || !location || pointForProvince(location)) return;
    issues.set(`${context}:${location}`, { location, context });
  };
  for (const year of report.years ?? []) for (const phase of year.phases ?? []) {
    for (const stateName of ['board_before', 'board_after']) {
      const board = phase[stateName] ?? {};
      for (const province of Object.keys(board.supply_ownership ?? {})) check(province, `${phase.phase_id}.${stateName}.supply_ownership`);
      for (const unit of board.units ?? []) check(unit.province, `${phase.phase_id}.${stateName}.units`);
      for (const province of board.standoff_provinces ?? []) check(province, `${phase.phase_id}.${stateName}.standoff_provinces`);
    }
    for (const mark of orderMarks(report, phase)) {
      check(mark.from, `${phase.phase_id}.${mark.order_id}.from`);
      check(mark.to, `${phase.phase_id}.${mark.order_id}.to`);
    }
  }
  return [...issues.values()];
}

/** Counts only public board deltas, never inferring causes from correspondence or prose. */
export function boardComparison(_report: any, phase: any): BoardComparison {
  const movements = phase.adjudication?.movement_outcomes ?? []; const adjustments = phase.adjudication?.adjustment_outcomes ?? [];
  const beforeOwners = phase.board_before?.supply_ownership ?? {}; const afterOwners = phase.board_after?.supply_ownership ?? {};
  const centers = new Set([...Object.keys(beforeOwners), ...Object.keys(afterOwners)]);
  const output = phase.adjudication?.output_state?.latest_public_resolution ?? phase.adjudication?.output_state ?? {};
  return {
    before_units: activeUnits(phase.board_before).length,
    after_units: activeUnits(phase.board_after).length,
    moves: movements.filter((item: any) => item.result === 'moved').length,
    dislodgements: movements.filter((item: any) => item.result === 'dislodged').length,
    adjustments: adjustments.length,
    center_changes: [...centers].filter((province) => beforeOwners[province] !== afterOwners[province]).length,
    standoffs: (output.standoff_provinces ?? phase.adjudication?.standoff_provinces ?? []).length
  };
}
