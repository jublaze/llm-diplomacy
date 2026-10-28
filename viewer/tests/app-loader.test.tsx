import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import game1 from '../fixtures/game_001/report.json';
import game2 from '../fixtures/game_002/report.json';
import { App } from '../src/app/App';
import { createNarrativeOutline, createNarrativePlan, createTemplateNarrative } from '../src/narrative-generator';
import { createInMemoryCatalog, validateCatalog, type FetchLike } from '../src/report-loader';

const fixtureData = createInMemoryCatalog({ game_001: game1 }, { fixture: true });
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 200 ? 'OK' : 'Not Found',
  text: async () => JSON.stringify(body),
});
const sha256 = async (text: string) => {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
};

afterEach(() => { cleanup(); window.location.hash = ''; });

describe('Phase 4B catalog-backed app loading', () => {
  it('loads the catalog first and fetches only the selected report', async () => {
    const fetcher = vi.fn<FetchLike>(async (url) => {
      if (url === 'https://viewer.test/reports/catalog.json') return response(fixtureData.catalog);
      if (url === 'https://viewer.test/reports/bundles/game_001/report.json') return response(game1);
      return response({}, 404);
    });
    render(<App catalogUrl="https://viewer.test/reports/catalog.json" fetcher={fetcher}/>);

    expect(screen.getByRole('heading', { name: /loading report catalog/i })).toBeVisible();
    fireEvent.click(await screen.findByRole('button', { name: /open game 1/i }));
    expect(screen.getByRole('heading', { name: /loading selected report/i })).toBeVisible();
    expect(await screen.findByRole('button', { name: /read 1901 chapter/i })).toBeVisible();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      'https://viewer.test/reports/catalog.json',
      'https://viewer.test/reports/bundles/game_001/report.json',
    ]);
  });

  it('shows actionable catalog and selected-report errors', async () => {
    const missing: FetchLike = async () => response({}, 404);
    const first = render(<App catalogUrl="https://viewer.test/missing.json" fetcher={missing}/>);
    expect(await screen.findByRole('heading', { name: /unable to load report catalog/i })).toBeVisible();
    expect(screen.getByRole('button', { name: /retry/i })).toBeVisible();
    first.unmount();

    const malformed: FetchLike = async (url) => url.endsWith('catalog.json')
      ? response(fixtureData.catalog)
      : response({ ...game1, schema_version: 'future' });
    render(<App catalogUrl="https://viewer.test/reports/catalog.json" fetcher={malformed}/>);
    fireEvent.click(await screen.findByRole('button', { name: /open game 1/i }));
    expect(await screen.findByRole('heading', { name: /unable to load selected report/i })).toBeVisible();
    expect(screen.getByText(/schema validation failed/i)).toBeVisible();
    expect(screen.getByRole('button', { name: /return to library/i })).toBeVisible();
  });

  it('preserves a direct phase and evidence deep link while lazily loading its report', async () => {
    const hash = '#game=game_001&phase=1902_spring_movement&evidence=message%3A1902_spring_negotiation_helena_0';
    window.location.hash = hash;
    const fetcher: FetchLike = async (url) => url.endsWith('catalog.json') ? response(fixtureData.catalog) : response(game1);
    render(<App catalogUrl="https://viewer.test/reports/catalog.json" fetcher={fetcher}/>);

    expect(await screen.findByRole('heading', { name: /Spring 1902 scene/i })).toBeVisible();
    expect(screen.getByRole('dialog', { name: /evidence drawer/i })).toBeVisible();
    expect(window.location.hash).toBe(hash);
  });

  it('resolves direct power/evidence state and caches reports while clearing stale game state', async () => {
    const data = createInMemoryCatalog({ game_001: game1, game_002: game2 }, { fixture: true });
    const sourceId = Object.keys(game1.evidence_index)[0];
    window.location.hash = `#game=game_001&view=powers&power=italy&evidence=source%3A${sourceId}`;
    const fetcher = vi.fn<FetchLike>(async (url) => {
      if (url.endsWith('catalog.json')) return response(data.catalog);
      if (url.includes('game_001')) return response(game1);
      if (url.includes('game_002')) return response(game2);
      return response({}, 404);
    });
    render(<App catalogUrl="https://viewer.test/reports/catalog.json" fetcher={fetcher}/>);

    expect(await screen.findByRole('heading', { name: /italy: power dossier/i })).toBeVisible();
    expect(screen.getByRole('dialog', { name: /evidence drawer/i })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /close evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /library/i }));
    fireEvent.click(screen.getByRole('button', { name: /open game 2/i }));
    expect(await screen.findByText(/Game 2 · 1901–1910/i)).toBeVisible();
    expect(window.location.hash).toBe('#game=game_002&view=home');
    expect(screen.queryByRole('dialog', { name: /evidence drawer/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /library/i }));
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    expect(await screen.findByText(/Game 1 · 1901–1910/i)).toBeVisible();

    expect(fetcher.mock.calls.filter(([url]) => url.includes('game_001'))).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url.includes('game_002'))).toHaveLength(1);
  });

  it('renders normalized text instead of standard geometry for an unsupported map', () => {
    const unsupported = structuredClone(game1) as any;
    unsupported.map.map_id = 'variant_without_geometry_v1';
    const data = createInMemoryCatalog({ game_001: unsupported }, { fixture: true });
    render(<App initialData={data}/>);
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /read 1901 chapter/i }));

    expect(screen.getByRole('heading', { name: /map visualization unavailable/i })).toBeVisible();
    expect(screen.getByText('variant_without_geometry_v1')).toBeVisible();
    expect(screen.queryByLabelText(/tactical map/i)).not.toBeInTheDocument();
  });

  it('rejects a mismatched narrative overlay while preserving the factual reader', async () => {
    const reportText = JSON.stringify(game1);
    const reportSha = await sha256(reportText);
    const plan = createNarrativePlan(game1, reportSha, 'omniscient_postgame');
    const narrative = createTemplateNarrative(plan, createNarrativeOutline(plan));
    narrative.audience = 'public';
    const narrativeText = JSON.stringify(narrative);
    const base = fixtureData.catalog.reports[0];
    const catalog = validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: [{ ...base, sha256: reportSha, narrative: {
      schema_version: 'historian_narrative_v1', narrative_url: 'narratives/game_001/approved.json', sha256: await sha256(narrativeText), report_sha256: reportSha, audience: 'omniscient_postgame',
    } }] });
    const fetcher: FetchLike = async (url) => {
      if (url.endsWith('catalog.json')) return response(catalog);
      if (url.includes('/narratives/')) return response(narrative);
      return response(game1);
    };
    render(<App catalogUrl="https://viewer.test/reports/catalog.json" fetcher={fetcher}/>);
    fireEvent.click(await screen.findByRole('button', { name: /open game 1/i }));

    expect(await screen.findByRole('heading', { name: /game 1: completed campaign/i })).toBeVisible();
    expect(await screen.findByText(/narrative overlay unavailable/i)).toBeVisible();
    expect(screen.getByText(/factual report remains available/i)).toBeVisible();
  });

  it('renders a valid approved overlay separately and keeps its citations interactive', async () => {
    const reportText = JSON.stringify(game1);
    const reportSha = await sha256(reportText);
    const plan = createNarrativePlan(game1, reportSha, 'omniscient_postgame');
    const narrative = createTemplateNarrative(plan, createNarrativeOutline(plan));
    const narrativeText = JSON.stringify(narrative);
    const base = fixtureData.catalog.reports[0];
    const catalog = validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: [{ ...base, sha256: reportSha, narrative: {
      schema_version: 'historian_narrative_v1', narrative_url: 'narratives/game_001/approved.json', sha256: await sha256(narrativeText), report_sha256: reportSha, audience: 'omniscient_postgame',
    } }] });
    const fetcher: FetchLike = async (url) => {
      if (url.endsWith('catalog.json')) return response(catalog);
      if (url.includes('/narratives/')) return response(narrative);
      return response(game1);
    };
    render(<App catalogUrl="https://viewer.test/reports/catalog.json" fetcher={fetcher}/>);
    fireEvent.click(await screen.findByRole('button', { name: /open game 1/i }));

    expect(await screen.findByRole('heading', { name: /approved historian narrative/i })).toBeVisible();
    fireEvent.click(screen.getAllByRole('button', { name: /open public evidence: spring 1901 source record/i })[0]);
    expect(screen.getByRole('dialog', { name: /evidence drawer/i })).toBeVisible();
  });
});
