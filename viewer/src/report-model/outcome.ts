export function normalizeOutcome(raw: { result?: string; final_phase_id?: string; solo_victor?: { agent_id?: string; power?: string; owned_centers?: number }; country_results?: Record<string, { agent_id: string; owned_centers: number }> }, finalYear: number, scheduledMaxYear?: number) {
  const leaders = Object.entries(raw.country_results ?? {}).map(([power, value]) => ({ agent_id: value.agent_id, power, supply_centers: value.owned_centers })).sort((a, b) => b.supply_centers - a.supply_centers);
  const leader = leaders[0];
  const claimed = raw.solo_victor;
  const hasFinalFallEvidence = Boolean(leader && claimed && [`${finalYear}_fall_movement`, `${finalYear}_fall_retreat`].includes(raw.final_phase_id ?? '') && claimed.power === leader.power && claimed.agent_id === leader.agent_id && claimed.owned_centers === leader.supply_centers && leader.supply_centers >= 18);
  const solo_victor = leader && raw.result === 'solo_victory' && hasFinalFallEvidence ? leader : null;
  const reachedScheduledCutoff = raw.result === 'completed' && Number.isSafeInteger(scheduledMaxYear) && finalYear === scheduledMaxYear;
  const termination = raw.result === 'draw' ? { kind: 'draw', label: 'Recorded draw', final_year: finalYear } : solo_victor ? { kind: 'solo_victory', label: `Solo victory by ${solo_victor.power[0].toUpperCase()}${solo_victor.power.slice(1)} (${solo_victor.supply_centers} supply centers)`, final_year: finalYear, final_phase_id: raw.final_phase_id } : raw.result === 'solo_victory' ? { kind: 'other_recorded_reason', label: 'Recorded solo-victory result lacks matching final Fall evidence', final_year: finalYear } : reachedScheduledCutoff ? { kind: 'scheduled_max_year', label: `Leader at the scheduled ${scheduledMaxYear} cutoff`, final_year: finalYear } : { kind: 'other_recorded_reason', label: 'Recorded end reason is not sufficient to classify a solo victory or draw', final_year: finalYear };
  return { termination, leaders: leader ? [leader] : [], solo_victor };
}
