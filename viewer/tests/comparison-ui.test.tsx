import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { App } from '../src/app/App';
import { createInMemoryCatalog } from '../src/report-loader';

const data = createInMemoryCatalog({ game_001: game1, game_002: game2 }, { fixture: true });
afterEach(() => { cleanup(); window.location.hash = ''; });

describe('Phase 5B comparison route', () => {
  it('renders accessible descriptive tables from a stable selected-games deep link', () => {
    window.location.hash = '#view=comparison&games=game_001%2Cgame_002';
    render(<App initialData={data}/>);

    expect(screen.getByRole('heading', { name: /compare recorded campaigns/i })).toBeVisible();
    expect(screen.getByRole('table', { name: /recorded campaign metrics/i })).toBeVisible();
    expect(screen.getByRole('table', { name: /supply-center changes by power/i })).toBeVisible();
    expect(screen.getByText(/no score, ranking, or causal claim/i)).toBeVisible();
    expect(screen.getByText(/selected reports: game 1 and game 2/i)).toBeVisible();
    expect(screen.getAllByRole('link', { name: /open source context/i })[0]).toHaveAttribute('href', expect.stringMatching(/^#game=game_001/));
  });

  it('updates selection by keyboard-operable controls and labels an insufficient comparison', () => {
    window.location.hash = '#view=comparison&games=game_001%2Cgame_002';
    render(<App initialData={data}/>);
    const game2Toggle = screen.getByRole('checkbox', { name: /game 2/i });
    game2Toggle.focus();
    fireEvent.keyDown(game2Toggle, { key: ' ' });
    fireEvent.click(game2Toggle);

    expect(game2Toggle).toHaveFocus();
    expect(window.location.hash).toContain('games=game_001');
    expect(screen.getByRole('alert')).toHaveTextContent(/select at least two reports/i);
  });
});
