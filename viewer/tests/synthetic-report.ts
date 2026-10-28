export function syntheticReport(start: number, end: number) {
  const years = [];
  for (let year = start; year <= end; year++) {
    const phases = ['spring', 'fall', 'winter'].map((season) => ({
      phase_id: `${year}_${season}_${season === 'winter' ? 'adjustment' : 'movement'}`, kind: season === 'winter' ? 'adjustment' : 'movement', year, season,
      board_before: { supply_ownership: {}, units: [] }, board_after: { supply_ownership: {}, units: [] },
      messages: [], orders: [], adjudication: {}, private_records: [],
      narrative: { overview: 'Synthetic validation record.', theater_sections: [], aftermath: '', source_refs: [] },
      source_refs: [], extensions: {},
    }));
    years.push({ year, phases });
  }
  return {
    schema_version: 'diplomacy_report_bundle_v1', bundle_id: `synthetic-${start}-${end}`,
    source: { experiment_id: 'synthetic', game_id: 'game_001', artifact_manifest_sha256: 'a'.repeat(64), visibility_mode: 'omniscient_postgame' },
    map: { map_id: 'standard_v1', geometry_version: 'standard_geometry_v1' },
    game: { title: 'Synthetic years', years_played: [start, end], outcome: { termination: { kind: 'other_recorded_reason', label: 'Synthetic test', final_year: end }, leaders: [], solo_victor: null }, provenance: {} },
    roster: [], summary: {}, strategic_arcs: [], years, messages: {}, orders: {}, private_records: {}, evidence_index: {}, extensions: {},
  };
}

export function syntheticFallSolo(report: ReturnType<typeof syntheticReport>, finalYear: number) {
  const year = report.years.find((entry) => entry.year === finalYear)!;
  year.phases = year.phases.filter((phase) => phase.season !== 'winter');
  const final = year.phases.find((phase) => phase.phase_id === `${finalYear}_fall_movement`)!;
  const ownership = Object.fromEntries(Array.from({ length: 18 }, (_, index) => [`center_${index}`, 'france']));
  final.board_after.supply_ownership = ownership;
  final.board_after.units = [];
  final.adjudication = { output_state: { supply_ownership: ownership, units: [] } };
  (report as any).roster = [
    { agent_id: 'agent_france', power: 'france' },
    { agent_id: 'agent_france', power: 'germany' },
  ];
  (report as any).game.outcome = {
    termination: { kind: 'solo_victory', label: 'France wins', final_year: finalYear, final_phase_id: final.phase_id },
    leaders: [{ agent_id: 'agent_france', power: 'france', supply_centers: 18 }],
    solo_victor: { agent_id: 'agent_france', power: 'france', supply_centers: 18 },
  };
  (report as any).game.provenance.raw_outcome = {
    result: 'solo_victory',
    country_results: {
      france: { agent_id: 'agent_france', owned_centers: 18 },
      germany: { agent_id: 'agent_france', owned_centers: 0 },
    },
  };
  return report;
}
