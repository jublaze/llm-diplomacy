export type Report = any;
export type Phase = any;
export type Message = any;
const seasonOrder: Record<string, number> = { spring: 0, fall: 1, winter: 2 };

export function phases(report: Report): Phase[] {
  return report.years.flatMap((year: any) => year.phases).sort((a: Phase, b: Phase) =>
    a.year - b.year || seasonOrder[a.season] - seasonOrder[b.season] || a.phase_id.localeCompare(b.phase_id));
}
export function findPhase(report: Report, phaseId: string): Phase | undefined { return phases(report).find((phase) => phase.phase_id === phaseId); }
export function phasesForYear(report: Report, year: number): Phase[] { return phases(report).filter((phase) => phase.year === year); }
export function defaultPhase(report: Report): string {
  const timeline = phases(report);
  return timeline.find((phase) => phase.kind === 'movement' && phase.season === 'spring')?.phase_id ?? timeline[0]?.phase_id ?? '';
}
export function phaseLabel(phaseId: string): string {
  const [year, season] = phaseId.split('_'); return `${season[0].toUpperCase()}${season.slice(1)} ${year}`;
}
export function phaseHref(gameId: string, phaseId: string): string { return `#game=${gameId}&phase=${phaseId}`; }
export type EvidenceTarget = { type: 'message' | 'source' | 'record' | 'order'; id: string };
export function evidenceHref(gameId: string, phaseId: string, target: EvidenceTarget): string {
  const params = new URLSearchParams({ game: gameId, phase: phaseId, evidence: `${target.type}:${target.id}` });
  return `#${params.toString()}`;
}
function negotiationId(phase: Phase): string { return `${phase.year}_${phase.season}_negotiation`; }
export function messageWindow(report: Report, phaseId: string): Message[] {
  const list = phases(report); const index = list.findIndex((phase) => phase.phase_id === phaseId); const selected = list[index];
  if (!selected) return []; const wanted = new Set([negotiationId(selected), ...(index > 0 ? [negotiationId(list[index - 1])] : [])]);
  return Object.values(report.messages).filter((message: any) => wanted.has(message.phase_id)).sort((a: any, b: any) => a.phase_id.localeCompare(b.phase_id) || a.chronological_position - b.chronological_position) as Message[];
}
export function centerCounts(board: any): Array<{ power: string; centers: number }> {
  const counts = new Map<string, number>(); Object.values(board.supply_ownership).forEach((owner) => counts.set(String(owner), (counts.get(String(owner)) ?? 0) + 1));
  return [...counts].map(([power, centers]) => ({ power, centers })).sort((a, b) => b.centers - a.centers || a.power.localeCompare(b.power));
}

export type SearchCategory = 'all' | 'power' | 'agent' | 'province' | 'phase' | 'source';
export type SearchResult = { kind: 'phase' | 'message' | 'record' | 'order' | 'source' | 'power'; id: string; phase_id?: string; title: string; excerpt: string };
export type ThreadFilters = { power?: string; phaseId?: string; channel?: string };
export type ThreadGroup = { thread_id: string; participants: string[]; messages: Message[]; latest_phase_id: string };
export type PowerSummary = { power: string; agent_id: string; display_name: string; profile_version: string; start_centers: number; final_centers: number; sent_messages: number; received_messages: number };

const provinceNames: Record<string, string> = {
  adr: 'Adriatic Sea', aeg: 'Aegean Sea', alb: 'Albania', ank: 'Ankara', arm: 'Armenia', bal: 'Baltic Sea', bar: 'Barents Sea', bel: 'Belgium', ber: 'Berlin', bla: 'Black Sea', boh: 'Bohemia', bre: 'Brest', bud: 'Budapest', bul: 'Bulgaria', bur: 'Burgundy', cly: 'Clyde', con: 'Constantinople', den: 'Denmark', eas: 'Eastern Mediterranean', edi: 'Edinburgh', eng: 'English Channel', fin: 'Finland', gal: 'Galicia', gas: 'Gascony', gol: 'Gulf of Lyon', gre: 'Greece', hel: 'Helgoland Bight', hol: 'Holland', ion: 'Ionian Sea', iri: 'Irish Sea', kie: 'Kiel', lon: 'London', lvp: 'Liverpool', lvn: 'Livonia', mao: 'Mid-Atlantic Ocean', mar: 'Marseilles', mos: 'Moscow', mun: 'Munich', nap: 'Naples', nao: 'North Atlantic Ocean', naf: 'North Africa', nth: 'North Sea', nwy: 'Norway', nwg: 'Norwegian Sea', par: 'Paris', pic: 'Picardy', pie: 'Piedmont', por: 'Portugal', pru: 'Prussia', rom: 'Rome', ruh: 'Ruhr', rum: 'Rumania', ser: 'Serbia', sev: 'Sevastopol', sil: 'Silesia', ska: 'Skagerrak', smy: 'Smyrna', spa: 'Spain', stp: 'St Petersburg', swe: 'Sweden', syr: 'Syria', tri: 'Trieste', tun: 'Tunis', tus: 'Tuscany', tys: 'Tyrrhenian Sea', ukr: 'Ukraine', ven: 'Venice', vie: 'Vienna', wal: 'Wales', war: 'Warsaw', wes: 'Western Mediterranean', yor: 'Yorkshire'
};
const normalize = (value: unknown) => String(value ?? '').toLowerCase();
const includes = (haystack: unknown, needle: string) => normalize(haystack).includes(needle);
const phaseSearchText = (phase: any) => {
  const provinces = new Set<string>([...Object.keys(phase.board_before.supply_ownership ?? {}), ...Object.keys(phase.board_after.supply_ownership ?? {}), ...(phase.board_before.units ?? []).map((unit: any) => unit.province), ...(phase.board_after.units ?? []).map((unit: any) => unit.province)]);
  return [...provinces].map((province) => `${province} ${provinceNames[province] ?? ''}`).join(' ');
};

/** Search source-backed fixture material without inventing an interpretation layer. */
export function searchReport(report: Report, rawQuery: string, category: SearchCategory = 'all'): SearchResult[] {
  const query = normalize(rawQuery).trim(); if (!query) return [];
  const rosterByAgent = new Map<string, any>((report.roster ?? []).map((member: any) => [member.agent_id, member]));
  const matches = (kind: SearchCategory, text: unknown) => (category === 'all' || category === kind || (category === 'source' && ['message', 'record', 'order', 'source'].includes(kind))) && includes(text, query);
  const results: SearchResult[] = [];
  for (const phase of phases(report)) {
    const phaseText = `${phase.phase_id} ${phaseLabel(phase.phase_id)} ${phaseSearchText(phase)}`;
    if (matches('phase', phaseText) || (category === 'province' && includes(phaseSearchText(phase), query))) results.push({ kind: 'phase', id: phase.phase_id, phase_id: phase.phase_id, title: phaseLabel(phase.phase_id), excerpt: phase.kind });
  }
  for (const message of Object.values(report.messages ?? {}) as Message[]) {
    const sender = rosterByAgent.get(message.sender_agent_id); const recipients = message.recipient_agent_ids.map((id: string) => rosterByAgent.get(id));
    const agentText = [message.sender_agent_id, sender?.display_name, sender?.power, ...message.recipient_agent_ids, ...recipients.flatMap((member: any) => [member?.display_name, member?.power])].join(' ');
    const text = `${agentText} ${message.phase_id} ${message.channel} ${message.text}`;
    const kind: SearchCategory = category === 'power' ? 'power' : category === 'agent' ? 'agent' : 'source';
    if ((category === 'all' && includes(text, query)) || matches(kind, text)) results.push({ kind: 'message', id: message.message_id, phase_id: message.phase_id, title: `Message · ${message.sender_agent_id} → ${message.recipient_agent_ids.join(', ')}`, excerpt: message.text });
  }
  for (const record of Object.values(report.private_records ?? {}) as any[]) {
    const member = rosterByAgent.get(record.agent_id); const text = `${record.agent_id} ${member?.display_name ?? ''} ${member?.power ?? ''} ${record.record_type} ${record.payload ? JSON.stringify(record.payload) : ''}`;
    const kind: SearchCategory = category === 'power' ? 'power' : category === 'agent' ? 'agent' : 'source';
    if ((category === 'all' && includes(text, query)) || matches(kind, text)) results.push({ kind: 'record', id: record.record_id, phase_id: record.record_id.split(':')[0], title: `Private record · ${record.agent_id}`, excerpt: record.record_type });
  }
  for (const [id, order] of Object.entries(report.orders ?? {}) as Array<[string, any]>) {
    const text = `${order.power} ${order.notation} ${JSON.stringify(order.payload ?? {})}`;
    const kind: SearchCategory = category === 'power' ? 'power' : category === 'province' ? 'province' : 'source';
    if ((category === 'all' && includes(text, query)) || matches(kind, text)) results.push({ kind: 'order', id, phase_id: id.split(':')[0], title: `Order · ${order.power}`, excerpt: order.notation ?? id });
  }
  for (const [id, source] of Object.entries(report.evidence_index ?? {}) as Array<[string, any]>) {
    const text = `${source.kind} ${source.visibility} ${source.artifact_path} ${source.json_pointer ?? ''}`;
    if (matches('source', text)) results.push({ kind: 'source', id, title: `Source · ${source.kind}`, excerpt: source.artifact_path });
  }
  for (const member of report.roster ?? []) {
    const text = `${member.power} ${member.agent_id} ${member.display_name} ${member.profile_version}`;
    if (matches(category === 'agent' ? 'agent' : 'power', text)) results.push({ kind: 'power', id: member.power, title: `Power dossier · ${member.power}`, excerpt: `${member.display_name} (${member.agent_id})` });
  }
  return results.slice(0, 100);
}

/** Group original correspondence by its normalized stable thread identifier. */
export function threadGroups(report: Report, filters: ThreadFilters = {}): ThreadGroup[] {
  const agentsForPower = new Set((report.roster ?? []).filter((member: any) => member.power === filters.power).map((member: any) => member.agent_id));
  const rank = new Map(phases(report).map((phase, index) => [phase.phase_id, index]));
  const messages = (Object.values(report.messages ?? {}) as Message[]).filter((message) =>
    (!filters.power || agentsForPower.has(message.sender_agent_id) || message.recipient_agent_ids.some((agent: string) => agentsForPower.has(agent))) &&
    (!filters.phaseId || message.phase_id === filters.phaseId) && (!filters.channel || message.channel === filters.channel));
  const byThread = new Map<string, Message[]>();
  for (const message of messages) byThread.set(message.thread_id, [...(byThread.get(message.thread_id) ?? []), message]);
  return [...byThread.entries()].map(([thread_id, threadMessages]) => {
    const sorted = threadMessages.sort((a, b) => (rank.get(a.phase_id) ?? 0) - (rank.get(b.phase_id) ?? 0) || a.chronological_position - b.chronological_position);
    return { thread_id, participants: [...new Set(sorted.flatMap((message) => [message.sender_agent_id, ...message.recipient_agent_ids]))].sort(), messages: sorted, latest_phase_id: sorted.at(-1)!.phase_id };
  }).sort((a, b) => (rank.get(b.latest_phase_id) ?? 0) - (rank.get(a.latest_phase_id) ?? 0) || a.thread_id.localeCompare(b.thread_id));
}

/** Derived dossier facts for any valid bundle; optional editorial notes stay optional. */
export function powerSummaries(report: Report): PowerSummary[] {
  const timeline = phases(report); const first = timeline[0]?.board_before; const final = timeline.at(-1)?.board_after;
  const starts = first ? centerCounts(first).reduce((totals, item) => ({ ...totals, [item.power]: item.centers }), {} as Record<string, number>) : {};
  const finals = final ? centerCounts(final).reduce((totals, item) => ({ ...totals, [item.power]: item.centers }), {} as Record<string, number>) : {};
  return (report.roster ?? []).map((member: any): PowerSummary => ({ power: member.power, agent_id: member.agent_id, display_name: member.display_name, profile_version: member.profile_version, start_centers: starts[member.power] ?? 0, final_centers: finals[member.power] ?? 0, sent_messages: Object.values(report.messages ?? {}).filter((message: any) => message.sender_agent_id === member.agent_id).length, received_messages: Object.values(report.messages ?? {}).filter((message: any) => message.recipient_agent_ids.includes(member.agent_id)).length })).sort((a: PowerSummary, b: PowerSummary) => b.final_centers - a.final_centers || a.power.localeCompare(b.power));
}
