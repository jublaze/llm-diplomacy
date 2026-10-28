import { useMemo, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { boardComparison, orderMarks, vectorGeometry, visualOutcome, type OrderKind, type OrderMark } from './analysis';
import { canonicalProvinceId, isSupplyCenter, pointForProvince, STANDARD_MAP_VIEWBOX } from './standard-geometry';

const graphicalKinds: OrderKind[] = ['move', 'hold', 'support_move', 'support_hold', 'convoy', 'retreat', 'build', 'disband'];
const routePaintKinds = new Set<OrderKind>(['move', 'support_move', 'support_hold', 'convoy', 'retreat']);
const orderLabels: Record<OrderKind, string> = { move: 'Moves', hold: 'Holds', support_move: 'Support moves', support_hold: 'Support holds', convoy: 'Convoys', retreat: 'Retreats', build: 'Builds', disband: 'Disbands', unsupported: 'Text only' };
const markSymbol: Partial<Record<OrderKind, string>> = { hold: 'H', support_move: 'S→', support_hold: 'S●', convoy: 'C', retreat: 'R', build: '+', disband: '−' };
const powerColors: Record<string, string> = { austria: '#9b486c', england: '#405b9a', france: '#287363', germany: '#565c62', italy: '#a96725', russia: '#944451', turkey: '#8b7725' };

const titleCase = (value: string) => value ? value[0].toUpperCase() + value.slice(1).replaceAll('_', ' ') : value;
const phaseName = (phase: any) => `${titleCase(phase.season)} ${phase.year} ${phase.kind}`;
const activeUnits = (board: any) => (board?.units ?? []).filter((unit: any) => unit.status !== 'destroyed');
const ownerStyle = (power: string): CSSProperties => ({ '--power-color': powerColors[power] ?? '#46585c' } as CSSProperties);
const selectedClass = (power: string, selected: string) => selected && power !== selected ? 'deemphasized' : selected ? 'emphasized' : '';

function routeOffsets(marks: OrderMark[]): Map<string, number> {
  const groups = new Map<string, OrderMark[]>();
  for (const mark of marks) {
    if (!mark.from || !mark.to) continue;
    const key = [canonicalProvinceId(mark.from) ?? mark.from, canonicalProvinceId(mark.to) ?? mark.to].sort().join(':');
    groups.set(key, [...(groups.get(key) ?? []), mark]);
  }
  const offsets = new Map<string, number>();
  for (const group of groups.values()) group.forEach((mark, index) => offsets.set(mark.order_id, index === 0 ? 0 : Math.ceil(index / 2) * (index % 2 ? 1 : -1)));
  return offsets;
}

function resultText(mark: OrderMark): string {
  const status = visualOutcome(mark);
  if (status === 'successful') return `confirmed ${mark.outcome}`;
  if (status === 'failed') return `did not succeed: ${mark.outcome}`;
  if (status === 'qualified') return `unconfirmed: ${mark.outcome}`;
  return mark.map_note ?? 'text only';
}

function outcomeLabel(mark: OrderMark): string {
  return `${mark.notation}; ${mark.kind.replace('_', ' ')}; adjudicated ${mark.outcome}.`;
}

function activateOrder(event: KeyboardEvent<SVGGElement>, open: () => void) {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
}

export function MapAnalysis({ report, phase, onOpenOrder }: { report: any; phase: any; onOpenOrder(orderId: string): void }) {
  const [boardState, setBoardState] = useState<'before' | 'after'>('after');
  const [selectedPower, setSelectedPower] = useState('');
  const [filters, setFilters] = useState<Record<OrderKind, boolean>>({ move:true, hold:true, support_move:true, support_hold:true, convoy:true, retreat:true, build:true, disband:true, unsupported:true });
  const allMarks = useMemo(() => orderMarks(report, phase), [report, phase]);
  const comparison = useMemo(() => boardComparison(report, phase), [report, phase]);
  const renderableMarks = allMarks.filter((mark) => mark.renderable && filters[mark.kind]);
  // Paint point markers first so their broad hit areas cannot obscure a route
  // that passes through the same province. Routes already receive offsets when
  // they overlap each other.
  const paintedMarks = [...renderableMarks].sort((left, right) => Number(routePaintKinds.has(left.kind)) - Number(routePaintKinds.has(right.kind)));
  const offsets = useMemo(() => routeOffsets(renderableMarks), [renderableMarks]);
  const board = boardState === 'before' ? phase.board_before : phase.board_after;
  const units = activeUnits(board);
  const owners = board.supply_ownership ?? {};
  const changedCenters = useMemo(() => {
    const before = phase.board_before?.supply_ownership ?? {}; const after = phase.board_after?.supply_ownership ?? {};
    return new Set([...new Set([...Object.keys(before), ...Object.keys(after)])].filter((province) => before[province] !== after[province]));
  }, [phase]);
  const standoffs = new Set<string>((board.standoff_provinces ?? phase.adjudication?.output_state?.latest_public_resolution?.standoff_provinces ?? phase.adjudication?.standoff_provinces ?? []) as string[]);
  const powers = useMemo(() => [...new Set([
    ...(report.roster ?? []).map((item: any) => item.power), ...allMarks.map((mark) => mark.power), ...Object.values(owners), ...units.map((unit: any) => unit.owner)
  ].filter(Boolean) as string[])].sort(), [report, allMarks, owners, units]);
  const textualOnly = allMarks.filter((mark) => !mark.renderable);
  const toggle = (kind: OrderKind) => setFilters((current) => ({ ...current, [kind]: !current[kind] }));
  const stateLabel = boardState === 'before' ? 'Before orders' : 'After adjudication';
  const mapLabel = `${stateLabel} map for ${phaseName(phase)} with ${renderableMarks.length} selected public order marks`;

  return <section className="analysis-map" aria-labelledby="analysis-map-heading">
    <div className="map-heading"><div><p className="eyebrow">Public board analysis</p><h2 id="analysis-map-heading">Public orders and adjudicated board</h2><p className="muted">The neutral board, ownership, units, and order treatments come only from this phase’s normalized public record. Narrative and evidence remain the explanation layer.</p></div><dl className="comparison-facts" aria-label="Before and after board comparison"><div><dt>Units</dt><dd>{comparison.before_units} → {comparison.after_units}</dd></div><div><dt>Moved</dt><dd>{comparison.moves}</dd></div><div><dt>Dislodged</dt><dd>{comparison.dislodgements}</dd></div><div><dt>Center changes</dt><dd>{comparison.center_changes}</dd></div><div><dt>Adjustments</dt><dd>{comparison.adjustments}</dd></div><div><dt>Standoffs</dt><dd>{comparison.standoffs}</dd></div></dl></div>
    <div className="map-controls"><fieldset><legend>Board state</legend><label><input type="radio" name="board-state" checked={boardState === 'before'} onChange={() => setBoardState('before')}/> Before orders</label><label><input type="radio" name="board-state" checked={boardState === 'after'} onChange={() => setBoardState('after')}/> After adjudication</label></fieldset><label className="map-power-filter">Emphasize power<select value={selectedPower} onChange={(event) => setSelectedPower(event.target.value)}><option value="">All powers</option>{powers.map((item) => <option value={item} key={item}>{titleCase(item)}</option>)}</select></label><fieldset><legend>Order layers</legend>{graphicalKinds.map((kind) => <label key={kind}><input type="checkbox" checked={filters[kind]} onChange={() => toggle(kind)}/>{orderLabels[kind]}</label>)}</fieldset></div>
    <div className="map-reader-layout">
      <div className="map-scroll" tabIndex={0} aria-label="Scrollable standard Diplomacy map">
        <svg className="analysis-board" viewBox={STANDARD_MAP_VIEWBOX} role="group" aria-label={mapLabel}><title>{mapLabel}</title><defs><marker id="map-arrow-success" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M0,0 L0,8 L9,4 z"/></marker><marker id="map-arrow-qualified" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M1,1 L1,7 L8,4 z"/></marker></defs><image href={`${import.meta.env.BASE_URL}maps/standard_clean.svg`} x="0" y="0" width="610" height="560" aria-hidden="true" pointerEvents="none"/>
          <g className="control-layer" aria-hidden="true">{Object.entries(owners).map(([province, value]) => { const point = pointForProvince(province); if (!point || !isSupplyCenter(province)) return null; const owner = String(value); return <g key={province} className={`control-marker ${selectedClass(owner, selectedPower)} ${changedCenters.has(province) ? 'changed' : ''}`} style={ownerStyle(owner)}><circle cx={point.x} cy={point.y} r="10"/><text x={point.x} y={point.y + 3}>{owner.slice(0, 1).toUpperCase()}</text>{changedCenters.has(province) && <circle className="change-ring" cx={point.x} cy={point.y} r="14"/>}</g>; })}{[...standoffs].map((province: string) => { const point = pointForProvince(province); return point ? <g className="standoff-marker" key={province}><line x1={point.x-7} y1={point.y-7} x2={point.x+7} y2={point.y+7}/><line x1={point.x+7} y1={point.y-7} x2={point.x-7} y2={point.y+7}/></g> : null; })}</g>
          <g className="order-layer">{paintedMarks.map((mark) => {
            const status = visualOutcome(mark); const vector = vectorGeometry(mark, offsets.get(mark.order_id) ?? 0); const point = pointForProvince(mark.to ?? mark.from ?? ''); const isRoute = Boolean(vector); const isDirectional = mark.kind === 'move' || mark.kind === 'retreat'; const label = `${outcomeLabel(mark)} ${resultText(mark)} Open public order evidence.`; const open = () => onOpenOrder(mark.order_id);
            return <g className={`order-mark ${mark.kind} ${status} ${selectedClass(mark.power, selectedPower)}`} data-visual-outcome={status} key={mark.order_id} role="button" tabIndex={0} aria-label={label} style={ownerStyle(mark.power)} pointerEvents="bounding-box" onClick={open} onKeyDown={(event) => activateOrder(event, open)}><title>{label}</title>
              {isRoute && vector ? <><path className="order-route-hit" d={vector.path}/><path className="order-route" d={vector.path} markerEnd={isDirectional && status === 'successful' ? 'url(#map-arrow-success)' : isDirectional && status === 'qualified' ? 'url(#map-arrow-qualified)' : undefined}/>{status === 'failed' && <g className="failed-end"><line x1={vector.end.x-5} y1={vector.end.y-5} x2={vector.end.x+5} y2={vector.end.y+5}/><line x1={vector.end.x+5} y1={vector.end.y-5} x2={vector.end.x-5} y2={vector.end.y+5}/></g>}{markSymbol[mark.kind] && <text className="order-symbol" x={vector.label.x} y={vector.label.y - 5}>{markSymbol[mark.kind]}</text>}</> : point ? <g className="point-order"><circle cx={point.x} cy={point.y} r="13"/><text x={point.x} y={point.y+5}>{markSymbol[mark.kind]}</text></g> : null}
            </g>;
          })}</g>
          <g className="unit-layer">{units.map((unit: any) => { const point = pointForProvince(unit.province); if (!point) return null; const displaced = unit.must_retreat || unit.status === 'dislodged'; return <g key={unit.unit_id} className={`unit ${selectedClass(unit.owner, selectedPower)} ${displaced ? 'dislodged' : ''}`} style={ownerStyle(unit.owner)} aria-label={`${unit.type === 'A' ? 'Army' : 'Fleet'} ${unit.owner} in ${unit.province}, ${unit.status}`}><circle cx={point.x} cy={point.y} r="9"/><text x={point.x} y={point.y + 3.5}>{unit.type}</text>{displaced && <text className="dislodged-mark" x={point.x + 9} y={point.y - 8}>!</text>}</g>; })}</g>
        </svg>
      </div>
      <section className="map-text-alternative" role="region" aria-labelledby="map-text-heading"><h3 id="map-text-heading">Map state and order text</h3><p className="map-status" aria-live="polite">{stateLabel} · {phaseName(phase)} · {units.length} active units · {renderableMarks.length} graphical orders{ textualOnly.length ? ` · ${textualOnly.length} textual-only` : ''}.</p><h4>Units and control</h4><details><summary>Open complete state list</summary><ul>{units.map((unit: any) => <li key={unit.unit_id}><b>{unit.type} {unit.province.toUpperCase()}</b> — {unit.owner}; {unit.status}</li>)}</ul><h4>Supply-center ownership</h4><p>{Object.entries(owners).map(([province, owner]) => `${province.toUpperCase()}: ${owner}`).join('; ') || 'No ownership records in this state.'}</p></details>
        <section className="map-order-index" aria-labelledby="map-order-index-heading"><h3 id="map-order-index-heading">Order and result index</h3><p className="muted">Complete textual equivalent. Filters alter the graphical layer only.</p><ul>{allMarks.map((mark) => <li key={mark.order_id} className={!mark.renderable ? 'not-mapped' : ''}><button onClick={() => onOpenOrder(mark.order_id)}><b>{mark.notation}</b><span>{orderLabels[mark.kind]} · {resultText(mark)}{!mark.renderable ? ` · not mapped: ${mark.map_note}` : ''}</span></button></li>)}</ul></section>
      </section>
    </div>
    <div className="map-legend" aria-label="Map legend"><span><b className="legend-line successful"/>confirmed movement</span><span><b className="legend-line failed"/>failed attempt, ending ×</span><span><b className="legend-line qualified"/>unconfirmed result</span><span><b className="legend-symbol">H</b>hold</span><span><b className="legend-symbol">S→</b>support move</span><span><b className="legend-symbol">S●</b>support hold</span><span><b className="legend-symbol">C</b>convoy</span><span><b className="legend-symbol">R</b>retreat</span><span><b className="legend-symbol">±</b>build / disband</span><span><b className="unit legend-unit">A</b>army; F = fleet</span></div>
  </section>;
}
