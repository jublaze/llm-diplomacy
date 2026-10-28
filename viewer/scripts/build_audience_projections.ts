import { createHash } from 'node:crypto';
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { auditProjection, isReportAudience, projectReport, type ReportAudience } from '../src/audience-projection.ts';
import { catalogEntryFromReport, validateCatalog, type ReportCatalogV1 } from '../src/report-loader.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import type { Report } from '../src/viewer-model.ts';

export type BuildAudienceProjectionOptions = {
  catalogPath: string;
  outputRoot: string;
  audiences?: ReportAudience[];
  allAudiences?: boolean;
  force?: boolean;
};

type ProjectionManifest = {
  schema_version: 'diplomacy_audience_projection_manifest_v1';
  source_catalog_sha256: string;
  audiences: Array<{
    audience: ReportAudience;
    reports: Array<{ game_id: string; source_sha256: string; projected_sha256: string; messages: number; private_records: number; evidence_sources: number }>;
  }>;
};

const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');

function replace(staging: string, destination: string, force: boolean) {
  if (existsSync(destination)) {
    if (!force) throw new Error(`Projection destination exists; pass --force to replace it: ${destination}`);
    rmSync(destination, { recursive: true, force: true });
  }
  try { renameSync(staging, destination); }
  catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'EPERM') throw error;
    cpSync(staging, destination, { recursive: true });
    rmSync(staging, { recursive: true, force: true });
  }
}

export function buildAudienceProjections(options: BuildAudienceProjectionOptions): ProjectionManifest {
  const catalogPath = resolve(options.catalogPath);
  const catalogRoot = dirname(catalogPath);
  const outputRoot = resolve(options.outputRoot);
  const sourceCatalogBytes = readFileSync(catalogPath);
  const sourceCatalog = validateCatalog(JSON.parse(sourceCatalogBytes.toString('utf8')));
  const loaded = sourceCatalog.reports.map((entry) => {
    if (entry.audience !== 'omniscient_postgame') throw new Error(`${entry.id}: projection source must be omniscient_postgame.`);
    const reportPath = resolve(catalogRoot, ...entry.report_url.split('/'));
    const bytes = readFileSync(reportPath);
    if (sha(bytes) !== entry.sha256) throw new Error(`${entry.id}: source report checksum mismatch.`);
    const report = JSON.parse(bytes.toString('utf8')) as Report;
    const errors = [...validateSchema(report), ...validateBundle(report)];
    if (errors.length) throw new Error(`${entry.id}: source report validation failed: ${errors.join('; ')}`);
    return { entry, report, bytes };
  });

  const rosterAgents = [...new Set(loaded.flatMap(({ report }) => report.roster.map((member: any) => member.agent_id as string)))].sort();
  const requested = options.allAudiences
    ? ['public', ...rosterAgents.map((id) => `player-${id}` as const), 'omniscient_postgame'] satisfies ReportAudience[]
    : options.audiences?.length ? [...new Set(options.audiences)] : ['public'] satisfies ReportAudience[];
  for (const audience of requested) if (!isReportAudience(audience)) throw new Error(`Invalid audience: ${audience}`);

  mkdirSync(dirname(outputRoot), { recursive: true });
  const staging = mkdtempSync(join(dirname(outputRoot), '.audience-projections-'));
  try {
    const manifest: ProjectionManifest = { schema_version: 'diplomacy_audience_projection_manifest_v1', source_catalog_sha256: sha(sourceCatalogBytes), audiences: [] };
    for (const audience of requested) {
      const audienceRoot = join(staging, audience);
      const entries = [];
      const reportManifest = [];
      for (const source of loaded) {
        if (audience.startsWith('player-') && !source.report.roster.some((member: any) => member.agent_id === audience.slice('player-'.length))) continue;
        const projected = projectReport(source.report, audience);
        const errors = [...validateSchema(projected), ...validateBundle(projected), ...auditProjection(source.report, projected, audience)];
        if (errors.length) throw new Error(`${source.entry.id}/${audience}: projection validation failed: ${errors.join('; ')}`);
        const bytes = audience === 'omniscient_postgame' ? source.bytes : Buffer.from(json(projected));
        const reportSha = sha(bytes);
        const destination = join(audienceRoot, 'bundles', source.entry.id, 'report.json');
        mkdirSync(dirname(destination), { recursive: true });
        if (audience === 'omniscient_postgame') copyFileSync(resolve(catalogRoot, ...source.entry.report_url.split('/')), destination);
        else writeFileSync(destination, bytes);
        entries.push(catalogEntryFromReport(source.entry.id, projected, { fixture: false, sha256: reportSha }));
        reportManifest.push({ game_id: source.entry.id, source_sha256: source.entry.sha256, projected_sha256: reportSha, messages: Object.keys(projected.messages).length, private_records: Object.keys(projected.private_records).length, evidence_sources: Object.keys(projected.evidence_index).length });
      }
      if (!entries.length) throw new Error(`${audience}: no source reports include this audience.`);
      const catalog: ReportCatalogV1 = validateCatalog({ schema_version: 'diplomacy_report_catalog_v1', reports: entries });
      writeFileSync(join(audienceRoot, 'catalog.json'), json(catalog), 'utf8');
      manifest.audiences.push({ audience, reports: reportManifest });
    }
    writeFileSync(join(staging, 'projection-manifest.json'), json(manifest), 'utf8');
    replace(staging, outputRoot, options.force ?? false);
    return manifest;
  } catch (error) {
    if (existsSync(staging)) rmSync(staging, { recursive: true, force: true });
    throw error;
  }
}

function option(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const catalogPath = option('--catalog');
  const outputRoot = option('--output-root');
  const audiences = option('--audiences')?.split(',').filter(Boolean) as ReportAudience[] | undefined;
  if (!catalogPath || !outputRoot || (!process.argv.includes('--all-audiences') && !audiences?.length)) {
    console.error('Usage: node --experimental-strip-types scripts/build_audience_projections.ts --catalog <catalog.json> --output-root <directory> (--audiences public,player-agent,omniscient_postgame | --all-audiences) [--force]');
    process.exitCode = 2;
  } else {
    try {
      const manifest = buildAudienceProjections({ catalogPath, outputRoot, audiences, allAudiences: process.argv.includes('--all-audiences'), force: process.argv.includes('--force') });
      console.log(`Built ${manifest.audiences.length} isolated audience catalogs at ${resolve(outputRoot)}.`);
    } catch (error) {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    }
  }
}
