import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import game1 from '../fixtures/game_001/report.json';
import { App } from '../src/app/App';
import { projectReport } from '../src/audience-projection';
import { createInMemoryCatalog } from '../src/report-loader';
import type { Report } from '../src/viewer-model';

afterEach(() => { cleanup(); window.location.hash = ''; });

describe('Phase 5A packaged perspective selection', () => {
  it('labels the selector as a reading perspective and explains unavailable evidence', () => {
    const publicReport = projectReport(game1 as unknown as Report, 'public');
    const omniscient = projectReport(game1 as unknown as Report, 'omniscient_postgame');
    const data = createInMemoryCatalog({
      'game_001--public': publicReport,
      'game_001--omniscient': omniscient,
    }, { fixture: true });
    window.location.hash = '#game=game_001--omniscient&view=home';
    render(<App initialData={data}/>);

    const selector = screen.getByRole('combobox', { name: /reading perspective/i });
    expect(selector).toHaveValue('game_001--omniscient');
    expect(screen.getByText(/not authentication/i)).toBeVisible();
    selector.focus();
    fireEvent.change(selector, { target: { value: 'game_001--public' } });

    expect(window.location.hash).toContain('game=game_001--public');
    expect(screen.getByText(/evidence unavailable in this perspective was removed before browser delivery/i)).toBeVisible();
    expect(selector).toHaveFocus();
  });
});
