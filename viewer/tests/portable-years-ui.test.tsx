import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { App } from '../src/app/App';
import { createInMemoryCatalog } from '../src/report-loader';
import { validateBundle } from '../src/report-model/validate';
import { syntheticFallSolo, syntheticReport } from './synthetic-report';

afterEach(() => { cleanup(); window.location.hash = ''; });

describe('portable year navigation', () => {
  it('renders an early campaign and opens its actual first phase', () => {
    const data = createInMemoryCatalog({ game_001: syntheticReport(1901, 1901) as any });
    render(<App initialData={data} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Game 1' }));
    expect(screen.getByText('1901–1901 campaign')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Read 1901 chapter' }));
    expect(screen.getByRole('heading', { name: 'Spring 1901 scene' })).toBeVisible();
  });

  it('renders and navigates a campaign ending in 1912', () => {
    const data = createInMemoryCatalog({ game_001: syntheticReport(1901, 1912) as any });
    render(<App initialData={data} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Game 1' }));
    expect(screen.getByText('1901–1912 campaign')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Read 1901 chapter' }));
    fireEvent.click(screen.getByRole('button', { name: 'Overview' }));
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Year chapters' })).getByRole('button', { name: '1912' }));
    expect(screen.getByRole('heading', { name: '1912: campaign record' })).toBeVisible();
    expect(within(screen.getByRole('navigation', { name: 'Phase selector' })).getByRole('button', { name: 'Fall 1912' })).toBeVisible();
  });

  it('presents a validated per-power solo for one agent assigned multiple powers', () => {
    const solo = syntheticFallSolo(syntheticReport(1901, 1901), 1901) as any;
    solo.roster = [
      { agent_id: 'agent_france', power: 'france', display_name: 'France player', profile_version: '1' },
      { agent_id: 'agent_france', power: 'germany', display_name: 'France player', profile_version: '1' },
    ];
    solo.game.outcome.termination.label = 'Solo victory by France (18 supply centers)';
    expect(validateBundle(solo)).toEqual([]);
    render(<App initialData={createInMemoryCatalog({ game_001: solo })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Game 1' }));
    const outcome = screen.getByText('Outcome').parentElement!;
    expect(within(outcome).getByText('Solo victory by France (18 supply centers)')).toBeVisible();
    expect(within(outcome).getByText('france / agent_france: 18 centers')).toBeVisible();
    expect(within(outcome).queryByText(/germany .* centers/)).not.toBeInTheDocument();
  });

  it('omits editorial board navigation without source-backed Fall evidence and targets real evidence when present', () => {
    const early = syntheticReport(1901, 1901) as any;
    early.summary.editorial = { executive_history: [], turning_points: [], power_dossiers: [] };
    const earlyData = createInMemoryCatalog({ game_001: early });
    render(<App initialData={earlyData} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Game 1' }));
    expect(screen.queryByRole('button', { name: /board evidence/i })).not.toBeInTheDocument();

    cleanup(); window.location.hash = '';
    const later = syntheticReport(1901, 1912) as any;
    later.summary.editorial = { executive_history: [], turning_points: [], power_dossiers: [] };
    const fall = later.years.find((year: any) => year.year === 1912).phases.find((phase: any) => phase.season === 'fall');
    fall.source_refs = ['fall-board'];
    later.evidence_index['fall-board'] = { source_id: 'fall-board', artifact_path: 'reports/game_001/boards/fall.json', sha256: 'a'.repeat(64), visibility: 'public', kind: 'board_snapshot' };
    render(<App initialData={createInMemoryCatalog({ game_001: later })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Game 1' }));
    const button = screen.getByRole('button', { name: 'Open Fall 1912 board evidence' });
    fireEvent.click(button);
    expect(window.location.hash).toContain('phase=1912_fall_movement');
    expect(screen.getByRole('heading', { name: 'Fall 1912 scene' })).toBeVisible();
  });
});
