import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { App } from '../src/app/App';
import { createInMemoryCatalog } from '../src/report-loader';

const fixtureData = createInMemoryCatalog({ game_001: game1, game_002: game2 }, { fixture: true });
const renderApp = () => render(<App initialData={fixtureData}/>);
const open1902Chapter = () => {
  fireEvent.click(screen.getByRole('button', { name: /read 1901 chapter/i }));
  fireEvent.click(screen.getByRole('button', { name: 'Overview' }));
  fireEvent.click(within(screen.getByRole('navigation', { name: /year chapters/i })).getByRole('button', { name: '1902' }));
};

afterEach(() => { cleanup(); window.location.hash = ''; });

describe('Phase 1 reader flow', () => {
  it('labels the primary chapter with the target year for short and historical bundles', () => {
    const oneYear = structuredClone(game1);
    oneYear.game.years_played = [1901, 1901];
    oneYear.game.outcome.termination.final_year = 1901;
    oneYear.game.outcome.termination.label = 'Leader at the scheduled 1901 cutoff';
    oneYear.years = oneYear.years.filter(({ year }) => year === 1901);
    const oneYearData = createInMemoryCatalog({ game_001: oneYear }, { fixture: true });
    render(<App initialData={oneYearData}/>);
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /read 1901 chapter/i }));
    expect(screen.getByRole('heading', { name: /1901: campaign record/i })).toBeVisible();
    expect(screen.getByRole('heading', { name: /Spring 1901 scene/i })).toBeVisible();

    cleanup();
    window.location.hash = '';
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    open1902Chapter();
    expect(screen.getByRole('heading', { name: /1902: a year in scenes/i })).toBeVisible();
  });

  it('opens Game 1 / 1902, switches scenes, and opens cited evidence', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    open1902Chapter();
    expect(screen.getByRole('heading', { name: /1902: a year in scenes/i })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /fall 1902/i }));
    expect(screen.getByRole('heading', { name: /Fall 1902 scene/i })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /open message evidence: message from helena to birsan/i }));
    expect(screen.getByRole('dialog', { name: /evidence drawer/i })).toBeVisible();
    expect(screen.getByText(/Correspondence context/)).toBeVisible();
  });

  it('renders editorial home evidence and all five proof sections', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    expect(screen.getByRole('heading', { name: /the campaign in evidence/i })).toBeVisible();
    expect(screen.getByRole('heading', { name: /patterns across the campaign/i })).toBeVisible();
    expect(screen.getByRole('heading', { name: /where the position changed/i })).toBeVisible();
    expect(screen.getByText(/finished first on 12 supply centers/i)).toBeVisible();
    fireEvent.click(screen.getAllByRole('button', { name: /open postgame evidence/i })[0]);
    expect(screen.getByText('Postgame evidence')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /close evidence/i }));
    open1902Chapter();
    for (const heading of ['A. Situation entering Spring', 'B. Intentions, beliefs, and plans', 'C. Diplomacy and commitments', 'D. Orders and adjudicated result', 'E. Reactions and what changed']) {
      expect(screen.getByRole('heading', { name: heading })).toBeVisible();
    }
  });

  it('opens message, public, and private cited material in the appropriate drawer', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    open1902Chapter();
    fireEvent.click(screen.getAllByRole('button', { name: /open message evidence: message from helena to birsan/i })[0]);
    expect(screen.getByRole('heading', { name: /message evidence/i })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /close evidence/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /open public evidence: spring 1902 board before-state/i })[0]);
    expect(screen.getByText('Public evidence')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /close evidence/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /open private_record evidence: birsan’s private spring reflection/i })[0]);
    expect(screen.getByRole('heading', { name: /private record/i })).toBeVisible();
  });

  it('keeps Game 2 on the generic compatibility fallback', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 2/i }));
    expect(screen.getByRole('heading', { name: /game 2: completed campaign/i })).toBeVisible();
    expect(screen.queryByText(/the campaign in evidence/i)).not.toBeInTheDocument();
  });
});

describe('Phase 2 generic explorer', () => {
  it('navigates every recorded phase in a later Game 2 year without game-specific markup', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 2/i }));
    fireEvent.click(within(screen.getByRole('navigation', { name: /year chapters/i })).getByRole('button', { name: '1906' }));
    expect(screen.getByRole('heading', { name: '1906: campaign record' })).toBeVisible();
    const phaseSelector = screen.getByRole('navigation', { name: /phase selector/i });
    expect(within(phaseSelector).getByRole('button', { name: 'Spring 1906' })).toBeVisible();
    fireEvent.click(within(phaseSelector).getByRole('button', { name: 'Fall 1906' }));
    expect(screen.getByRole('heading', { name: 'Fall 1906 scene' })).toBeVisible();
  });

  it('searches source-backed records and opens a message through an evidence deep link', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Search$/ }));
    fireEvent.change(screen.getByLabelText(/search power, agent, province/i), { target: { value: 'helena' } });
    fireEvent.change(screen.getByLabelText('Search scope'), { target: { value: 'agent' } });
    const open = screen.getAllByRole('button', { name: 'Open result' })[0];
    fireEvent.click(open);
    expect(screen.getByRole('dialog', { name: /evidence drawer/i })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Message evidence' })).toBeVisible();
    expect(window.location.hash).toContain('view=search');
    expect(window.location.hash).toContain('evidence=message%3A');
  });

  it('filters original correspondence and preserves source metadata when a message is opened', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Threads$/ }));
    fireEvent.change(screen.getByLabelText('Power'), { target: { value: 'italy' } });
    fireEvent.change(screen.getByLabelText('Phase'), { target: { value: '1902_spring_negotiation' } });
    fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'private' } });
    fireEvent.click(screen.getByText('birsan ↔ helena'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Open message evidence' })[0]);
    expect(screen.getByText('Source ID')).toBeVisible();
    expect(window.location.hash).toContain('evidence=message%3A');
  });

  it('derives generic power dossiers and board trajectories when editorial arcs are absent', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 2/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Powers$/ }));
    expect(screen.getByRole('heading', { name: 'Power dossiers' })).toBeVisible();
    expect(screen.getAllByText('Centers').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /^Arcs$/ }));
    expect(screen.getByRole('heading', { name: 'Strategic arcs' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'russia: board trajectory' })).toBeVisible();
  });

  it('resolves a stable direct evidence URL and returns to the same phase on close', () => {
    window.location.hash = '#game=game_001&phase=1902_spring_movement&evidence=message%3A1902_spring_negotiation_helena_0';
    renderApp();
    expect(screen.getByRole('heading', { name: '1902: a year in scenes' })).toBeVisible();
    expect(screen.getByRole('dialog', { name: /evidence drawer/i })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /close evidence/i }));
    expect(window.location.hash).toBe('#game=game_001&phase=1902_spring_movement');
  });
});

describe('Phase 3 map analysis', () => {
  it('compares before and after boards, filters map marks, and opens an exact public order', () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    open1902Chapter();
    expect(screen.getByRole('heading', { name: /public orders and adjudicated board/i })).toBeVisible();
    expect(screen.getByText('Order and result index')).toBeVisible();
    expect(screen.getByRole('group', { name: /after adjudication map/i })).toBeVisible();
    fireEvent.click(screen.getByLabelText('Before orders'));
    expect(screen.getByRole('group', { name: /before orders map/i })).toBeVisible();
    fireEvent.change(screen.getByLabelText('Emphasize power'), { target: { value: 'italy' } });
    fireEvent.click(screen.getByLabelText('Moves'));
    expect(screen.getByRole('group', { name: /18 selected public order marks/i })).toBeVisible();
    fireEvent.click(screen.getByLabelText('Moves'));
    fireEvent.change(screen.getByLabelText('Emphasize power'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^A bud–gal; move;/ }));
    const drawer = screen.getByRole('dialog', { name: /evidence drawer/i });
    expect(drawer).toBeVisible();
    expect(within(drawer).getByRole('heading', { name: /public order/i })).toBeVisible();
    expect(within(drawer).getByText('A bud–gal')).toBeVisible();
  });

  it('updates the map for movement, retreat, and adjustment phases', () => {
    window.location.hash = '#game=game_001&phase=1902_spring_movement';
    renderApp();
    expect(screen.getByRole('group', { name: /after adjudication map.*spring 1902 movement/i })).toBeVisible();
    fireEvent.click(within(screen.getByRole('navigation', { name: /phase selector/i })).getByRole('button', { name: /spring retreat · 1902/i }));
    expect(screen.getByRole('group', { name: /after adjudication map.*spring 1902 retreat/i })).toBeVisible();
    fireEvent.click(within(screen.getByRole('navigation', { name: /phase selector/i })).getByRole('button', { name: /^winter 1902$/i }));
    expect(screen.getByRole('group', { name: /after adjudication map.*winter 1902 adjustment/i })).toBeVisible();
    expect(screen.getByLabelText('Builds')).toBeChecked();
    expect(screen.getByLabelText('Disbands')).toBeChecked();
  });

  it('keeps Game 2 on the generic standard-map renderer and exposes a complete text alternative', () => {
    window.location.hash = '#game=game_002&phase=1902_spring_movement';
    renderApp();
    expect(screen.getByRole('heading', { name: /public orders and adjudicated board/i })).toBeVisible();
    const alternative = screen.getByRole('region', { name: /map state and order text/i });
    expect(within(alternative).getByRole('heading', { name: /units and control/i })).toBeVisible();
    expect(within(alternative).getByRole('heading', { name: /order and result index/i })).toBeVisible();
  });

  it('opens evidence from a keyboard-focused vector and identifies failed movement without a success arrow', () => {
    window.location.hash = '#game=game_001&phase=1902_spring_movement';
    renderApp();
    const failed = screen.getByRole('button', { name: /^A gal–bud; move; adjudicated dislodged/i });
    expect(failed).toHaveAttribute('data-visual-outcome', 'failed');
    expect(failed.querySelector('path')).not.toHaveAttribute('marker-end', 'url(#map-arrow-success)');
    fireEvent.keyDown(failed, { key: 'Enter' });
    const drawer = screen.getByRole('dialog', { name: /evidence drawer/i });
    expect(drawer).toBeVisible();
    expect(within(drawer).getByRole('heading', { name: /^public order$/i })).toBeVisible();
  });
});
