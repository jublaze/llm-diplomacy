import { validateSchema } from './report-model/validate-schema.ts';
import { validateBundle } from './report-model/validate.ts';
import { validateHistorianNarrative, validateHistorianNarrativeSchema, type HistorianNarrativeV1, type NarrativeAudience } from './narrative-model.ts';
import { isReportAudience, type ReportAudience } from './audience-projection.ts';
import { resolveBundleAudience, type BundleAudienceMode } from './report-model/audience.ts';
import type { Report } from './viewer-model.ts';

export const REPORT_CATALOG_SCHEMA = 'diplomacy_report_catalog_v1' as const;

export type ReportCatalogEntry = {
  id: string;
  source_game_id?: string;
  experiment_id?: string;
  artifact_manifest_sha256?: string;
  bundle_id: string;
  label: string;
  title: string;
  years_played: [number, number];
  outcome_label: string;
  schema_version: string;
  audience: ReportAudience;
  audience_mode?: BundleAudienceMode;
  audience_policy_version?: string;
  audience_description?: string;
  approved_for_static_public_distribution?: boolean;
  map_id: string;
  report_url: string;
  sha256: string;
  fixture: boolean;
  narrative?: NarrativeCatalogReference;
};

export type NarrativeCatalogReference = {
  schema_version: 'historian_narrative_v1';
  narrative_url: string;
  sha256: string;
  report_sha256: string;
  audience: NarrativeAudience;
};

export type ReportCatalogV1 = {
  schema_version: typeof REPORT_CATALOG_SCHEMA;
  reports: ReportCatalogEntry[];
};

export type AppInitialData = {
  catalog: ReportCatalogV1;
  reports: Record<string, Report>;
  url?: string;
};

export type FetchResponseLike = {
  ok: boolean;
  status: number;
  statusText: string;
  text(): Promise<string>;
};

export type FetchLike = (url: string) => Promise<FetchResponseLike>;

export class ReportLoadError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ReportLoadError';
  }
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const safeRelativeUrl = (value: unknown): value is string => {
  if (typeof value !== 'string' || !value.trim()) return false;
  if (/^[A-Za-z]:[\\/]/.test(value) || value.startsWith('/') || value.startsWith('\\')) return false;
  if (/^[a-z][a-z\d+.-]*:/i.test(value)) return false;
  return !value.replaceAll('\\', '/').split('/').includes('..');
};

const containsAbsoluteLocalPath = (value: string) => /(?:^|[\s"'])(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/]|\/(?:Users|home|var|tmp)\/)/i.test(value);

const gameLabel = (id: string) => {
  const match = /^game_(\d+)$/.exec(id);
  return match ? `Game ${Number(match[1])}` : id;
};

const audienceLabel = (audience: ReportAudience) => audience === 'public'
  ? 'Public'
  : audience === 'omniscient_postgame'
    ? 'Omniscient postgame'
    : `Player ${audience.slice('player-'.length)}`;

export const readerTitle = (report: Report) => {
  const technicalTitle = `${report.source.experiment_id} / ${report.source.game_id}`;
  return report.game.title === technicalTitle ? `${gameLabel(report.source.game_id)}: completed campaign` : report.game.title;
};

export function catalogEntryFromReport(id: string, report: Report, options: { fixture?: boolean; sha256?: string } = {}): ReportCatalogEntry {
  const years = report.game?.years_played;
  if (!Array.isArray(years) || years.length !== 2) throw new ReportLoadError(`Report ${id} has invalid years_played metadata.`);
  const sourceGameId = report.source.game_id;
  const audience = report.source.visibility_mode as ReportAudience;
  const audienceMetadata = resolveBundleAudience(report);
  return {
    id,
    ...(sourceGameId !== id ? { source_game_id: sourceGameId } : {}),
    experiment_id: report.source.experiment_id,
    artifact_manifest_sha256: report.source.artifact_manifest_sha256,
    bundle_id: report.bundle_id,
    label: sourceGameId === id ? gameLabel(id) : `${gameLabel(sourceGameId)} · ${audienceLabel(audience)}`,
    title: readerTitle(report),
    years_played: [years[0], years[1]],
    outcome_label: report.game.outcome.termination.label,
    schema_version: report.schema_version,
    audience,
    audience_mode: audienceMetadata.mode,
    audience_policy_version: audienceMetadata.policy_version,
    audience_description: audienceMetadata.reader_description,
    approved_for_static_public_distribution: audienceMetadata.approved_for_static_public_distribution && !audienceMetadata.legacy_inference,
    map_id: report.map.map_id,
    report_url: `bundles/${id}/report.json`,
    sha256: options.sha256 ?? '',
    fixture: options.fixture ?? false,
  };
}

export function validateCatalog(value: unknown): ReportCatalogV1 {
  if (!isObject(value)) throw new ReportLoadError('Report catalog must be a JSON object.');
  if (value.schema_version !== REPORT_CATALOG_SCHEMA) {
    throw new ReportLoadError(`Unsupported catalog schema ${JSON.stringify(value.schema_version)}; expected ${REPORT_CATALOG_SCHEMA}.`);
  }
  if (!Array.isArray(value.reports)) throw new ReportLoadError('Report catalog reports must be an array.');
  const ids = new Set<string>();
  for (const [index, candidate] of value.reports.entries()) {
    if (!isObject(candidate)) throw new ReportLoadError(`Catalog report ${index} must be an object.`);
    for (const field of ['id', 'bundle_id', 'label', 'title', 'outcome_label', 'schema_version', 'audience', 'map_id', 'report_url', 'sha256']) {
      if (typeof candidate[field] !== 'string') throw new ReportLoadError(`Catalog report ${index} has invalid ${field}.`);
    }
    if (typeof candidate.fixture !== 'boolean') throw new ReportLoadError(`Catalog report ${index} has invalid fixture flag.`);
    if (candidate.sha256 !== '' && !/^[a-f\d]{64}$/.test(candidate.sha256 as string)) throw new ReportLoadError(`Catalog report ${index} has invalid sha256.`);
    if (!Array.isArray(candidate.years_played) || candidate.years_played.length !== 2 || candidate.years_played.some((year) => !Number.isSafeInteger(year)) || candidate.years_played[0] < 1901 || candidate.years_played[0] > candidate.years_played[1]) {
      throw new ReportLoadError(`Catalog report ${index} has invalid years_played.`);
    }
    if (ids.has(candidate.id as string)) throw new ReportLoadError(`Duplicate report id ${candidate.id}.`);
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(candidate.id as string)) throw new ReportLoadError(`Catalog report ${index} has unsafe report id.`);
    if (candidate.source_game_id !== undefined && (typeof candidate.source_game_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(candidate.source_game_id))) throw new ReportLoadError(`Catalog report ${index} has unsafe source_game_id.`);
    if (candidate.experiment_id !== undefined && (typeof candidate.experiment_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(candidate.experiment_id))) throw new ReportLoadError(`Catalog report ${index} has invalid experiment_id.`);
    if (candidate.artifact_manifest_sha256 !== undefined && (typeof candidate.artifact_manifest_sha256 !== 'string' || !/^[a-f\d]{64}$/.test(candidate.artifact_manifest_sha256))) throw new ReportLoadError(`Catalog report ${index} has invalid artifact_manifest_sha256.`);
    for (const field of ['label', 'title', 'outcome_label']) {
      if (containsAbsoluteLocalPath(candidate[field] as string)) throw new ReportLoadError(`Catalog report ${index} ${field} contains an absolute local path.`);
    }
    ids.add(candidate.id as string);
    if (!safeRelativeUrl(candidate.report_url)) throw new ReportLoadError(`Catalog report ${candidate.id} must use a safe relative report_url.`);
    if (candidate.schema_version !== 'diplomacy_report_bundle_v1') throw new ReportLoadError(`Catalog report ${candidate.id} uses unsupported report schema.`);
    if (!isReportAudience(candidate.audience)) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid report audience.`);
    const audienceFields = ['audience_mode', 'audience_policy_version', 'audience_description', 'approved_for_static_public_distribution'];
    const presentAudienceFields = audienceFields.filter((field) => candidate[field] !== undefined);
    if (presentAudienceFields.length && presentAudienceFields.length !== audienceFields.length) throw new ReportLoadError(`Catalog report ${candidate.id} has incomplete audience-safety metadata.`);
    if (presentAudienceFields.length) {
      if (!['omniscient_research', 'postgame_public', 'full_postgame_public'].includes(candidate.audience_mode as string)) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid audience_mode.`);
      if (typeof candidate.audience_policy_version !== 'string' || !candidate.audience_policy_version) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid audience_policy_version.`);
      if (typeof candidate.audience_description !== 'string' || !candidate.audience_description.trim()) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid audience_description.`);
      if (typeof candidate.approved_for_static_public_distribution !== 'boolean') throw new ReportLoadError(`Catalog report ${candidate.id} has invalid static-public approval flag.`);
      if (candidate.approved_for_static_public_distribution !== (['postgame_public', 'full_postgame_public'].includes(candidate.audience_mode as string))) throw new ReportLoadError(`Catalog report ${candidate.id} has contradictory audience safety metadata.`);
    }
    if (candidate.narrative !== undefined) {
      if (!isObject(candidate.narrative)) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid narrative reference.`);
      if (candidate.narrative.schema_version !== 'historian_narrative_v1') throw new ReportLoadError(`Catalog report ${candidate.id} uses unsupported narrative schema.`);
      if (!safeRelativeUrl(candidate.narrative.narrative_url)) throw new ReportLoadError(`Catalog report ${candidate.id} must use a safe relative narrative_url.`);
      for (const field of ['sha256', 'report_sha256']) if (typeof candidate.narrative[field] !== 'string' || !/^[a-f\d]{64}$/.test(candidate.narrative[field] as string)) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid narrative ${field}.`);
      if (!['public', 'omniscient_postgame'].includes(candidate.narrative.audience as string)) throw new ReportLoadError(`Catalog report ${candidate.id} has invalid narrative audience.`);
      if (candidate.narrative.report_sha256 !== candidate.sha256) throw new ReportLoadError(`Catalog report ${candidate.id} narrative/report checksum binding does not match.`);
      if (candidate.narrative.audience !== candidate.audience) throw new ReportLoadError(`Catalog report ${candidate.id} narrative audience does not match report audience.`);
    }
  }
  return value as unknown as ReportCatalogV1;
}

export function createInMemoryCatalog(reports: Record<string, Report>, options: { fixture?: boolean } = {}): AppInitialData {
  const entries = Object.entries(reports)
    .map(([id, report]) => catalogEntryFromReport(id, report, options))
    .sort((left, right) => left.id.localeCompare(right.id));
  const catalog = validateCatalog({ schema_version: REPORT_CATALOG_SCHEMA, reports: entries });
  return { catalog, reports, url: 'https://fixtures.invalid/reports/catalog.json' };
}

async function fetchJson(url: string, fetcher: FetchLike, kind: 'catalog' | 'report'): Promise<{ value: unknown; text: string }> {
  let response: FetchResponseLike;
  try {
    response = await fetcher(url);
  } catch (error) {
    throw new ReportLoadError(`${kind[0].toUpperCase()}${kind.slice(1)} request failed: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
  }
  if (!response.ok) throw new ReportLoadError(`${kind[0].toUpperCase()}${kind.slice(1)} request failed with HTTP ${response.status} ${response.statusText}.`);
  try {
    const text = await response.text();
    return { value: JSON.parse(text), text };
  } catch (error) {
    throw new ReportLoadError(`${kind[0].toUpperCase()}${kind.slice(1)} response was not valid JSON.`, { cause: error });
  }
}

export async function loadCatalog(catalogUrl: string, fetcher: FetchLike, baseUrl?: string): Promise<{ catalog: ReportCatalogV1; url: string }> {
  const fallbackBase = baseUrl ?? (typeof window === 'undefined' ? 'http://localhost/' : window.location.href);
  const url = new URL(catalogUrl, fallbackBase).href;
  const { value } = await fetchJson(url, fetcher, 'catalog');
  return { catalog: validateCatalog(value), url };
}

export async function loadCatalogReport(entry: ReportCatalogEntry, catalogUrl: string, fetcher: FetchLike): Promise<Report> {
  const url = new URL(entry.report_url, catalogUrl).href;
  const { value, text } = await fetchJson(url, fetcher, 'report');
  if (entry.sha256) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    const actual = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
    if (actual !== entry.sha256) throw new ReportLoadError(`Report checksum mismatch: expected ${entry.sha256}, received ${actual}.`);
  }
  const schemaErrors = validateSchema(value);
  if (schemaErrors.length) throw new ReportLoadError(`Report schema validation failed: ${schemaErrors.join('; ')}`);
  const semanticErrors = validateBundle(value);
  if (semanticErrors.length) throw new ReportLoadError(`Report semantic validation failed: ${semanticErrors.join('; ')}`);
  const report = value as Report;
  if (report.game.years_played[0] !== entry.years_played[0] || report.game.years_played[1] !== entry.years_played[1]) throw new ReportLoadError('Catalog/report played-year range mismatch.');
  if (report.bundle_id !== entry.bundle_id) throw new ReportLoadError(`Catalog/report identity mismatch: expected bundle ${entry.bundle_id}, received ${report.bundle_id}.`);
  const expectedGameId = entry.source_game_id ?? entry.id;
  if (report.source?.game_id !== expectedGameId) throw new ReportLoadError(`Catalog/report identity mismatch: expected game ${expectedGameId}, received ${report.source?.game_id}.`);
  if (report.source?.visibility_mode !== entry.audience) throw new ReportLoadError(`Catalog/report audience mismatch: expected ${entry.audience}, received ${report.source?.visibility_mode}.`);
  const metadata = resolveBundleAudience(report);
  if (entry.audience_mode !== undefined) {
    if (metadata.mode !== entry.audience_mode || metadata.policy_version !== entry.audience_policy_version || metadata.reader_description !== entry.audience_description || (metadata.approved_for_static_public_distribution && !metadata.legacy_inference) !== entry.approved_for_static_public_distribution) {
      throw new ReportLoadError('Catalog/report audience-safety metadata mismatch.');
    }
  }
  return report;
}

export async function loadCatalogNarrative(entry: ReportCatalogEntry, catalogUrl: string, fetcher: FetchLike, report: Report): Promise<HistorianNarrativeV1> {
  if (!entry.narrative) throw new ReportLoadError(`Catalog report ${entry.id} has no narrative overlay.`);
  const url = new URL(entry.narrative.narrative_url, catalogUrl).href;
  const { value, text } = await fetchJson(url, fetcher, 'report');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  const actual = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  if (actual !== entry.narrative.sha256) throw new ReportLoadError(`Narrative checksum mismatch: expected ${entry.narrative.sha256}, received ${actual}.`);
  const schemaErrors = validateHistorianNarrativeSchema(value);
  if (schemaErrors.length) throw new ReportLoadError(`Narrative schema validation failed: ${schemaErrors.join('; ')}`);
  const semanticErrors = validateHistorianNarrative(value, report, { reportSha256: entry.narrative.report_sha256, audience: entry.narrative.audience });
  if (semanticErrors.length) throw new ReportLoadError(`Narrative semantic validation failed: ${semanticErrors.join('; ')}`);
  return value as HistorianNarrativeV1;
}
