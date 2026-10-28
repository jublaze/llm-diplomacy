import { defaultPhase, findPhase } from './viewer-model.ts';

export function archiveNavigationTarget(report: any) {
  const phase = findPhase(report, defaultPhase(report));
  if (!phase) throw new Error('Report has no default phase for archive navigation.');
  return { year: phase.year, phaseId: phase.phase_id, chapterButton: `Read ${phase.year} chapter`, heading: phase.year === 1902 ? '1902: a year in scenes' : `${phase.year}: campaign record` };
}
