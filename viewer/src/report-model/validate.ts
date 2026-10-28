import { resolveBundleAudience } from './audience.ts';

type Bundle = Record<string, any>;
const fail = (errors: string[], path: string, message: string) => errors.push(`${path}: ${message}`);
const object = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const canonical = (value: any): any => Array.isArray(value)
  ? value.map(canonical)
  : object(value)
    ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]))
    : value;

/** Validates bundle invariants that JSON Schema alone cannot express. */
export function validateBundle(bundle: unknown): string[] {
  const errors: string[] = [];
  if (!object(bundle)) return ['/: expected an object'];
  for (const key of ['bundle_id', 'source', 'map', 'game', 'roster', 'summary', 'years', 'messages', 'orders', 'private_records', 'evidence_index']) {
    if (!(key in bundle)) fail(errors, `/${key}`, 'is required');
  }
  if (bundle.schema_version !== 'diplomacy_report_bundle_v1') fail(errors, '/schema_version', 'must be diplomacy_report_bundle_v1');
  if (!Array.isArray(bundle.years)) return [...errors, '/years: expected an array'];
  const sources = object(bundle.evidence_index) ? bundle.evidence_index : {};
  const messages = object(bundle.messages) ? bundle.messages : {};
  const records = object(bundle.private_records) ? bundle.private_records : {};
  const orders = object(bundle.orders) ? bundle.orders : {};
  const citationKinds: Record<string, string[]> = { public: ['board_snapshot', 'orders', 'adjudication'], message: ['message'], private_record: ['private_record'], postgame: ['report'] };
  const audience = bundle.source?.visibility_mode;
  let audienceMetadata: ReturnType<typeof resolveBundleAudience> | null = null;
  try { audienceMetadata = resolveBundleAudience(bundle); }
  catch (error) { fail(errors, '/extensions/audience', error instanceof Error ? error.message : String(error)); }
  const explicitPostgamePublic = audienceMetadata?.mode === 'postgame_public' && !audienceMetadata.legacy_inference;
  if (audienceMetadata && !audienceMetadata.legacy_inference) {
    const expectedVisibility = audienceMetadata.mode === 'postgame_public' ? 'public' : 'omniscient_postgame';
    if (audience !== expectedVisibility) fail(errors, '/source/visibility_mode', `must be ${expectedVisibility} for ${audienceMetadata.mode}`);
  }
  const playerAgent = typeof audience === 'string' && audience.startsWith('player-') ? audience.slice('player-'.length) : null;
  if (audience !== 'public' && audience !== 'omniscient_postgame' && !playerAgent) fail(errors, '/source/visibility_mode', 'must be public, player-<agent-id>, or omniscient_postgame');
  if (playerAgent && !(bundle.roster ?? []).some((member: any) => member?.agent_id === playerAgent)) fail(errors, '/source/visibility_mode', 'player audience must identify a roster agent');
  const validateCitation = (citation: unknown, path: string) => {
    if (!object(citation)) { fail(errors, path, 'citation must be an object'); return; }
    const source = sources[citation.source_ref];
    if (!source) { fail(errors, `${path}/source_ref`, 'must resolve to evidence'); return; }
    if (!citationKinds[citation.evidence_type]?.includes(source.kind)) fail(errors, `${path}/evidence_type`, 'does not match evidence kind');
    if (!object(citation.target) || typeof citation.target.id !== 'string') { fail(errors, `${path}/target`, 'must identify a target'); return; }
    if (citation.target.type === 'source' && citation.target.id !== citation.source_ref) fail(errors, `${path}/target`, 'source target must equal source_ref');
    if (citation.target.type === 'message' && (!messages[citation.target.id] || messages[citation.target.id].source_ref !== citation.source_ref)) fail(errors, `${path}/target`, 'message target must resolve to the cited source');
    if (citation.target.type === 'record' && (!records[citation.target.id] || records[citation.target.id].source_ref !== citation.source_ref)) fail(errors, `${path}/target`, 'record target must resolve to the cited source');
    if (citation.target.type === 'order' && (!orders[citation.target.id] || orders[citation.target.id].source_ref !== citation.source_ref)) fail(errors, `${path}/target`, 'order target must resolve to the cited source');
  };
  const validateClaim = (item: unknown, path: string) => {
    if (!object(item) || typeof item.text !== 'string' || !item.text.trim()) { fail(errors, path, 'claim must have non-empty text'); return; }
    if (!Array.isArray(item.citations) || !item.citations.length) { fail(errors, `${path}/citations`, 'claim needs at least one citation'); return; }
    item.citations.forEach((citation: unknown, index: number) => validateCitation(citation, `${path}/citations/${index}`));
  };
  const validateClaims = (items: unknown, path: string) => {
    if (!Array.isArray(items) || !items.length) { fail(errors, path, 'must contain at least one cited claim'); return; }
    items.forEach((item, index) => validateClaim(item, `${path}/${index}`));
  };
  const validPath = (path: unknown) => typeof path === 'string' && path.length > 0 && !path.includes('\\') && !path.startsWith('/') && !/^[A-Za-z]:/.test(path) && !/^[a-z]+:\/\//i.test(path) && !path.split('/').includes('..');
  for (const [id, source] of Object.entries(sources)) {
    if (!object(source)) { fail(errors, `/evidence_index/${id}`, 'must be an object'); continue; }
    if (!validPath(source.artifact_path)) fail(errors, `/evidence_index/${id}/artifact_path`, 'must be a relative path inside the experiment root');
    if (source.json_pointer !== undefined) {
      const pointer = source.json_pointer;
      const releasedTranscript = source.kind === 'message' && source.release_context === 'postgame_released_negotiation_transcript';
      const validPointer = source.kind === 'message' ? typeof pointer === 'string' && (releasedTranscript ? /^\/messages\/\d+$/.test(pointer) : /^\/line\/[1-9]\d*$/.test(pointer)) : typeof pointer === 'string' && (pointer === '' || pointer.startsWith('/'));
      if (!validPointer) fail(errors, `/evidence_index/${id}/json_pointer`, 'has invalid pointer syntax for its artifact kind');
      if (source.release_context !== undefined && (source.kind !== 'message' || source.release_context !== 'postgame_released_negotiation_transcript')) fail(errors, `/evidence_index/${id}/release_context`, 'is only valid for a postgame-released negotiation transcript');
    }
  }
  const yearsPlayed = bundle.game?.years_played;
  const outcomeFinalYear = bundle.game?.outcome?.termination?.final_year;
  const outcome = bundle.game?.outcome;
  const soloTermination = outcome?.termination?.kind === 'solo_victory';
  const finalPhaseId = outcome?.termination?.final_phase_id;
  const finalSolo = outcome?.solo_victor;
  let declaredYearRange: [number, number] | null = null;
  if (!Array.isArray(yearsPlayed) || yearsPlayed.length !== 2 ||
      !yearsPlayed.every((year: unknown) => Number.isSafeInteger(year))) {
    fail(errors, '/game/years_played', 'must declare safe integer start and final years');
  } else {
    const [startYear, endYear] = yearsPlayed as number[];
    if (startYear < 1901 || startYear > endYear) {
      fail(errors, '/game/years_played', 'must be an ascending range beginning in 1901 or later');
    } else {
      declaredYearRange = [startYear, endYear];
      if (!Number.isSafeInteger(outcomeFinalYear) || outcomeFinalYear !== endYear) {
        fail(errors, '/game/outcome/termination/final_year', 'must equal the declared final played year');
      }
    }
  }
  const seenYears = new Set<number>();
  let previousYear: number | null = null;
  for (const [yearIndex, year] of bundle.years.entries()) {
    const p = `/years/${yearIndex}`;
    if (!object(year) || !Number.isSafeInteger(year.year) || !Array.isArray(year.phases)) { fail(errors, p, 'must contain safe integer year and phases array'); continue; }
    if (seenYears.has(year.year)) fail(errors, p + '/year', 'duplicates a declared year');
    else seenYears.add(year.year);
    if (declaredYearRange && (year.year < declaredYearRange[0] || year.year > declaredYearRange[1])) fail(errors, p + '/year', 'falls outside the declared played-year range');
    if (previousYear === null) {
      if (declaredYearRange && year.year !== declaredYearRange[0]) fail(errors, p + '/year', 'must begin at the declared first played year');
    } else if (year.year !== previousYear + 1) fail(errors, p + '/year', 'must follow the previous played year without a gap or reordering');
    previousYear = year.year;
    const seenPhaseIds = new Set<string>();
    const seenPhaseIdentities = new Set<string>();
    for (const [phaseIndex, phase] of year.phases.entries()) {
      const q = `${p}/phases/${phaseIndex}`;
      if (!object(phase)) { fail(errors, q, 'must be an object'); continue; }
      for (const field of ['phase_id', 'kind', 'board_before', 'board_after', 'messages', 'orders', 'private_records', 'narrative', 'source_refs']) if (!(field in phase)) fail(errors, `${q}/${field}`, 'is required');
      if (!Number.isSafeInteger(phase.year) || phase.year !== year.year) fail(errors, `${q}/year`, `must equal parent year ${year.year}`);
      const phaseId = typeof phase.phase_id === 'string' ? /^([0-9]{4,})_(spring|fall|winter)_(movement|retreat|adjustment)$/.exec(phase.phase_id) : null;
      if (!phaseId) fail(errors, `${q}/phase_id`, 'must encode its year, season, and normalized phase kind');
      else {
        const [, idYearText, idSeason, idKind] = phaseId;
        const idYear = Number(idYearText);
        if (!Number.isSafeInteger(idYear) || idYear !== year.year || phase.year !== idYear || phase.season !== idSeason || phase.kind !== idKind) {
          fail(errors, `${q}/phase_id`, 'must agree with the parent year and phase year, season, and kind');
        }
        if (seenPhaseIds.has(phase.phase_id)) fail(errors, `${q}/phase_id`, 'duplicates a phase in this year');
        seenPhaseIds.add(phase.phase_id);
      }
      if (!['spring', 'fall', 'winter'].includes(phase.season)) fail(errors, `${q}/season`, 'must be spring, fall, or winter');
      if (!['movement', 'retreat', 'adjustment'].includes(phase.kind)) fail(errors, `${q}/kind`, 'must be movement, retreat, or adjustment');
      if ((phase.season === 'winter' && phase.kind !== 'adjustment') ||
          ((phase.season === 'spring' || phase.season === 'fall') && !['movement', 'retreat'].includes(phase.kind))) {
        fail(errors, `${q}/kind`, 'is not a normalized phase kind for this season');
      }
      if (['spring', 'fall', 'winter'].includes(phase.season) && ['movement', 'retreat', 'adjustment'].includes(phase.kind)) {
        const identity = `${phase.season}:${phase.kind}`;
        if (seenPhaseIdentities.has(identity)) fail(errors, q, `duplicates semantic phase ${phase.season} ${phase.kind}`);
        seenPhaseIdentities.add(identity);
      }
      const phaseSourceRefs = Array.isArray(phase.source_refs) ? phase.source_refs : [];
      const narrativeSourceRefs = Array.isArray(phase.narrative?.source_refs) ? phase.narrative.source_refs : [];
      if (!Array.isArray(phase.source_refs)) fail(errors, `${q}/source_refs`, 'must be an array');
      for (const ref of [...phaseSourceRefs, ...narrativeSourceRefs]) if (!sources[ref]) fail(errors, q, `unresolved evidence source ${JSON.stringify(ref)}`);
      for (const id of Array.isArray(phase.messages) ? phase.messages : []) if (!messages[id]) fail(errors, `${q}/messages`, `unresolved message ${JSON.stringify(id)}`);
      for (const id of Array.isArray(phase.orders) ? phase.orders : []) if (!orders[id]) fail(errors, `${q}/orders`, `unresolved order ${JSON.stringify(id)}`);
      for (const id of Array.isArray(phase.private_records) ? phase.private_records : []) if (!records[id]) fail(errors, `${q}/private_records`, `unresolved private record ${JSON.stringify(id)}`);
      const editorial = phase.narrative?.editorial;
      if (editorial !== undefined) {
        if (!Array.isArray(editorial.sections) || editorial.sections.length !== 5) fail(errors, `${q}/narrative/editorial/sections`, 'must contain exactly five sections');
        else editorial.sections.forEach((section: any, sectionIndex: number) => {
          const sectionPath = `${q}/narrative/editorial/sections/${sectionIndex}`;
          if (!object(section) || typeof section.heading !== 'string' || !section.heading.trim()) fail(errors, sectionPath, 'section must have a heading');
          validateClaims(section?.claims, `${sectionPath}/claims`);
          validateClaim(section?.bridge, `${sectionPath}/bridge`);
        });
        const words = (editorial.sections ?? []).flatMap((section: any) => [...(section.claims ?? []), section.bridge]).map((item: any) => item?.text ?? '').join(' ').trim().split(/\s+/).filter(Boolean).length;
        const range = phase.season === 'winter' ? [350, 650] : [500, 850];
        if (words < range[0] || words > range[1]) fail(errors, `${q}/narrative/editorial`, `contains ${words} words; expected ${range[0]}–${range[1]}`);
      }
    }
    const seasonRank: Record<string, number> = { spring: 0, fall: 1, winter: 2 };
    const kindRank: Record<string, number> = { movement: 0, retreat: 1, adjustment: 0 };
    const canonicalPhases = [...year.phases].sort((left: any, right: any) =>
      (seasonRank[left?.season] ?? 99) - (seasonRank[right?.season] ?? 99) ||
      (kindRank[left?.kind] ?? 99) - (kindRank[right?.kind] ?? 99) ||
      String(left?.phase_id ?? '').localeCompare(String(right?.phase_id ?? '')));
    if (yearIndex === bundle.years.length - 1 && soloTermination && canonicalPhases.some((phase, index) => phase !== year.phases[index])) {
      fail(errors, `${p}/phases`, 'must follow the same chronological order used by the reader');
    }
    const isFinalYear = yearIndex === bundle.years.length - 1;
    const finalSoloPhase = isFinalYear && soloTermination && typeof finalPhaseId === 'string'
      ? year.phases.find((phase: any) => phase?.phase_id === finalPhaseId)
      : null;
    const isValidFinalFallSolo = Boolean(
      object(finalSolo) &&
      finalSoloPhase &&
      year.phases[year.phases.length - 1] === finalSoloPhase &&
      finalPhaseId === `${year.year}_fall_${finalSoloPhase.kind}` &&
      ['movement', 'retreat'].includes(finalSoloPhase.kind) &&
      finalSoloPhase.season === 'fall'
    );
    const requiredPhases: Array<[string, string]> = [['spring', 'movement'], ['fall', 'movement']];
    if (!isFinalYear || !isValidFinalFallSolo) requiredPhases.push(['winter', 'adjustment']);
    for (const [season, kind] of requiredPhases) {
      if (!seenPhaseIdentities.has(`${season}:${kind}`)) fail(errors, p, `missing ${season} ${kind} phase`);
    }
    if (isFinalYear && soloTermination && !isValidFinalFallSolo) {
      fail(errors, '/game/outcome/termination/final_phase_id', 'must identify the final Fall movement or retreat phase');
    }
    if (isFinalYear && soloTermination && isValidFinalFallSolo) {
      const ownership = finalSoloPhase.board_after?.supply_ownership;
      const snapshotUnits = finalSoloPhase.board_after?.units;
      const adjudicated = object(finalSoloPhase.adjudication?.output_state) ? finalSoloPhase.adjudication.output_state : null;
      const owners = object(ownership) ? Object.values(ownership) : [];
      const centers = object(finalSolo) && object(ownership) ? owners.filter((owner) => owner === finalSolo.power).length : -1;
      const ownersAreValid = owners.every((owner) => owner === null || typeof owner === 'string');
      const boardWinners = [...new Set(owners.filter((owner): owner is string => typeof owner === 'string'))]
        .filter((power) => owners.filter((owner) => owner === power).length >= 18);
      const rosterMember = Array.isArray(bundle.roster)
        ? bundle.roster.find((member: any) => member?.agent_id === finalSolo.agent_id && member?.power === finalSolo.power)
        : undefined;
      if (!object(finalSolo) || typeof finalSolo.power !== 'string' || typeof finalSolo.agent_id !== 'string' ||
          !Number.isSafeInteger(finalSolo.supply_centers) || finalSolo.supply_centers < 18 ||
          !ownersAreValid || boardWinners.length !== 1 || boardWinners[0] !== finalSolo.power ||
          centers !== finalSolo.supply_centers || !rosterMember || rosterMember.power !== finalSolo.power) {
        fail(errors, '/game/outcome/solo_victor', 'must match the final Fall board and assigned roster power with at least 18 centers');
      }
      if (!adjudicated || !object(adjudicated.supply_ownership) || !Array.isArray(adjudicated.units) ||
          JSON.stringify(canonical(adjudicated.supply_ownership)) !== JSON.stringify(canonical(ownership)) ||
          JSON.stringify(canonical(adjudicated.units)) !== JSON.stringify(canonical(snapshotUnits))) {
        fail(errors, `/years/${yearIndex}/phases/${year.phases.indexOf(finalSoloPhase)}/adjudication/output_state`, 'must agree with the final snapshot ownership and units');
      }
      const leaders = outcome?.leaders;
      if (!Array.isArray(leaders) || leaders.length !== 1 || !object(leaders[0]) ||
          leaders[0].agent_id !== finalSolo.agent_id || leaders[0].power !== finalSolo.power || leaders[0].supply_centers !== finalSolo.supply_centers) {
        fail(errors, '/game/outcome/leaders', 'must contain only the evidenced solo victor with matching power, agent, and center count');
      }
      const rawCountries = bundle.game?.provenance?.raw_outcome?.country_results;
      if (rawCountries !== undefined) {
        if (!object(rawCountries)) fail(errors, '/game/provenance/raw_outcome/country_results', 'must be an object when present');
        else {
          for (const [power, result] of Object.entries(rawCountries)) {
            const actualCenters = owners.filter((owner) => owner === power).length;
            const assigned = Array.isArray(bundle.roster) && bundle.roster.some((member: any) => member?.agent_id === (result as any)?.agent_id && member?.power === power);
            if (!object(result) || typeof result.agent_id !== 'string' || !Number.isSafeInteger(result.owned_centers) ||
                result.owned_centers !== actualCenters || !assigned) {
              fail(errors, `/game/provenance/raw_outcome/country_results/${power}`, 'must match final board ownership and an assigned agent/power');
            }
          }
          for (const power of new Set(owners.filter((owner): owner is string => typeof owner === 'string'))) {
            if (!Object.hasOwn(rawCountries, power)) {
              fail(errors, `/game/provenance/raw_outcome/country_results/${power}`, 'is required for final board ownership');
            }
          }
        }
      }
      if (!Array.isArray(snapshotUnits) || snapshotUnits.some((unit: any) => unit?.must_retreat || unit?.status === 'dislodged') ||
          !Array.isArray(adjudicated?.units) || adjudicated.units.some((unit: any) => unit?.must_retreat || unit?.status === 'dislodged')) {
        fail(errors, '/game/outcome/solo_victor', 'cannot be declared while units still require retreat resolution');
      }
    } else if (!soloTermination && finalSolo != null) {
      fail(errors, '/game/outcome/solo_victor', 'must be null unless termination is a solo victory');
    }
  }
  if (declaredYearRange) {
    const expectedCount = declaredYearRange[1] - declaredYearRange[0] + 1;
    if (bundle.years.length !== expectedCount) fail(errors, '/years', 'must contain exactly one entry for every declared played year');
    if (previousYear !== declaredYearRange[1]) fail(errors, '/years', 'must end at the declared final played year');
  }
  for (const [id, message] of Object.entries(messages)) {
    if (!object(message)) { fail(errors, `/messages/${id}`, 'must be an object'); continue; }
    if (!message.text) fail(errors, `/messages/${id}/text`, 'must preserve original non-empty text');
    if (!sources[message.source_ref]) fail(errors, `/messages/${id}/source_ref`, `unresolved evidence source ${JSON.stringify(message.source_ref)}`);
    else if (sources[message.source_ref].visibility !== message.visibility) fail(errors, `/messages/${id}/source_ref`, 'evidence visibility must match message visibility');
    if (audience === 'public' && message.visibility !== 'public') {
      const evidence = sources[message.source_ref];
      if (!explicitPostgamePublic || evidence?.release_context !== 'postgame_released_negotiation_transcript') fail(errors, `/messages/${id}`, 'public projection can contain a private-at-the-time message only as an explicit postgame-released transcript');
    }
    if (playerAgent && message.visibility !== 'public' && message.sender_agent_id !== playerAgent && !message.recipient_agent_ids?.includes(playerAgent)) fail(errors, `/messages/${id}`, 'player projection contains another player’s private message');
  }
  for (const [id, order] of Object.entries(orders)) {
    if (!object(order)) { fail(errors, `/orders/${id}`, 'must be an object'); continue; }
    if (!sources[order.source_ref]) fail(errors, `/orders/${id}/source_ref`, `unresolved evidence source ${JSON.stringify(order.source_ref)}`);
    else if (sources[order.source_ref].visibility !== 'public') fail(errors, `/orders/${id}/source_ref`, 'order evidence must be public');
  }
  for (const [id, record] of Object.entries(records)) {
    if (!object(record)) { fail(errors, `/private_records/${id}`, 'must be an object'); continue; }
    if (!sources[record.source_ref]) fail(errors, `/private_records/${id}/source_ref`, `unresolved evidence source ${JSON.stringify(record.source_ref)}`);
    else if (sources[record.source_ref].visibility !== 'private') fail(errors, `/private_records/${id}/source_ref`, 'private record evidence must be private');
    if (audience === 'public') fail(errors, `/private_records/${id}`, 'public projection cannot contain private records');
    if (playerAgent && record.agent_id !== playerAgent) fail(errors, `/private_records/${id}`, 'player projection contains another player’s private record');
  }
  if (audience === 'public' || playerAgent) {
    const allowedPrivateSources = new Set<string>([
      ...Object.values(messages).filter((message: any) => message.visibility === 'private').map((message: any) => message.source_ref),
      ...Object.values(records).map((record: any) => record.source_ref),
    ]);
    for (const [id, source] of Object.entries(sources) as Array<[string, any]>) {
      if (source.visibility === 'omniscient_postgame') fail(errors, `/evidence_index/${id}`, 'projected bundle cannot contain omniscient evidence');
      if (source.visibility === 'private' && !allowedPrivateSources.has(id)) fail(errors, `/evidence_index/${id}`, 'projected bundle contains unreferenced private evidence');
      if (audience === 'public' && source.visibility !== 'public') {
        const releasedMessage = explicitPostgamePublic && source.visibility === 'private' && source.kind === 'message' && source.release_context === 'postgame_released_negotiation_transcript' && allowedPrivateSources.has(id);
        if (!releasedMessage) fail(errors, `/evidence_index/${id}`, 'public projection can contain private evidence only for an explicit postgame-released negotiation transcript');
      }
    }
  }
  if (explicitPostgamePublic && object(bundle.extensions?.postgame_records) && Object.keys(bundle.extensions.postgame_records).length) fail(errors, '/extensions/postgame_records', 'postgame_public projection cannot contain unreviewed postgame records');
  const editorial = bundle.summary?.editorial;
  if (editorial !== undefined) {
    validateClaims(editorial.executive_history, '/summary/editorial/executive_history');
    if (!Array.isArray(editorial.turning_points) || editorial.turning_points.length < 3) fail(errors, '/summary/editorial/turning_points', 'must contain at least three turning points');
    else editorial.turning_points.forEach((arc: any, index: number) => validateClaims(arc?.claims, `/summary/editorial/turning_points/${index}/claims`));
    if (!Array.isArray(editorial.power_dossiers) || editorial.power_dossiers.length !== bundle.roster?.length) fail(errors, '/summary/editorial/power_dossiers', 'must contain one dossier per roster member');
    else editorial.power_dossiers.forEach((dossier: any, index: number) => validateClaim(dossier?.note, `/summary/editorial/power_dossiers/${index}/note`));
  }
  if (!Array.isArray(bundle.strategic_arcs)) fail(errors, '/strategic_arcs', 'must be an array');
  else bundle.strategic_arcs.forEach((arc: any, index: number) => validateClaims(arc?.claims, `/strategic_arcs/${index}/claims`));
  return errors;
}
