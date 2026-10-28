import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import legacyReport from '../fixtures/game_001/report.json';
import { App } from '../src/app/App';
import { createInMemoryCatalog } from '../src/report-loader';
import { isStaticPublicBundle, resolveBundleAudience } from '../src/report-model/audience';
import { validateBundle } from '../src/report-model/validate';
import { searchReport, type Report } from '../src/viewer-model';

const repositoryRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const generatedPath = join(
  repositoryRoot,
  'fixtures',
  'phase5a_generated',
  'reports',
  'xai_grok_4_5_rules_v5_campaign_v1_3_persistent_advanced_roster_round_robin_2x_1910',
  'game_001',
  'postgame_public',
  'report.json',
);
const publicReport = JSON.parse(readFileSync(generatedPath, 'utf8')) as Report;

afterEach(() => { cleanup(); window.location.hash = ''; });

describe('Phase 5A audience safety', () => {
  it('treats legacy bundles as research-only and accepts explicit postgame-public metadata', () => {
    expect(resolveBundleAudience(legacyReport)).toMatchObject({
      mode: 'omniscient_research',
      approved_for_static_public_distribution: false,
      legacy_inference: true,
    });
    expect(isStaticPublicBundle(legacyReport)).toBe(false);
    expect(resolveBundleAudience(publicReport)).toMatchObject({
      mode: 'postgame_public',
      approved_for_static_public_distribution: true,
      legacy_inference: false,
    });
    expect(isStaticPublicBundle(publicReport)).toBe(true);
    expect(validateBundle(publicReport)).toEqual([]);
  });

  it('fails safely on unknown audience metadata and disallows unreleased private evidence', () => {
    const unknown = structuredClone(publicReport) as any;
    unknown.extensions.audience.mode = 'power_limited';
    expect(validateBundle(unknown).join('\n')).toMatch(/unknown audience mode/i);

    const unsafe = structuredClone(publicReport) as any;
    const privateMessage = Object.values(unsafe.messages).find((message: any) => message.visibility === 'private') as any;
    delete unsafe.evidence_index[privateMessage.source_ref].release_context;
    expect(validateBundle(unsafe).join('\n')).toMatch(/postgame-released negotiation transcript/i);
  });

  it('has closed public search/evidence data and labels messages as released after play', () => {
    expect(publicReport.private_records).toEqual({});
    expect(Object.values(publicReport.evidence_index).every((source: any) => source.visibility !== 'omniscient_postgame')).toBe(true);
    expect(Object.values(publicReport.evidence_index).filter((source: any) => source.visibility === 'private').every((source: any) => source.release_context === 'postgame_released_negotiation_transcript')).toBe(true);
    expect(searchReport(publicReport, 'reflection', 'all').some((result) => result.kind === 'record')).toBe(false);

    render(<App initialData={createInMemoryCatalog({ game_001: publicReport })}/>);
    fireEvent.click(screen.getByRole('button', { name: /open game 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /^threads$/i }));
    expect(screen.getByText('Postgame-released negotiation transcript')).toBeVisible();
    expect(screen.getByText(/not public facts during play/i)).toBeVisible();
    expect(screen.queryByText('Original correspondence')).not.toBeInTheDocument();
  });
});
