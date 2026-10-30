import { createHash } from 'node:crypto';
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { REPORT_CATALOG_SCHEMA, catalogEntryFromReport, validateCatalog, type ReportCatalogV1 } from '../src/report-loader.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import { validateHistorianNarrative, validateHistorianNarrativeSchema, type NarrativeAudience } from '../src/narrative-model.ts';
import type { Report } from '../src/viewer-model.ts';
import { isStaticPublicBundle } from '../src/report-model/audience.ts';

export type BuildReportCatalogOptions = {
  sourceRoot: string;
  outputRoot: string;
  fixture?: boolean;
  force?: boolean;
  approvedNarrativeRoot?: string;
  requirePublicSafe?: boolean;
  experimentCatalogIds?: boolean;
};

const inside = (parent: string, child: string) => {
  const path = relative(resolve(parent), resolve(child));
  return path === '' || (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path));
};

function reportPaths(root: string): string[] {
  const found: string[] = [];
  const visit = (directory: string) => {
    for (const item of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(directory, item.name);
      if (item.isDirectory()) visit(path);
      else if (item.isFile() && item.name === 'report.json') found.push(path);
    }
  };
  visit(root);
  return found;
}

export function buildReportCatalog(options: BuildReportCatalogOptions): ReportCatalogV1 {
  const sourceRoot = resolve(options.sourceRoot);
  const outputRoot = resolve(options.outputRoot);
  if (!existsSync(sourceRoot) || !statSync(sourceRoot).isDirectory()) throw new Error(`Catalog source root does not exist or is not a directory: ${sourceRoot}`);
  if (inside(sourceRoot, outputRoot) || inside(outputRoot, sourceRoot)) throw new Error('Catalog source and output roots must not overlap.');

  const paths = reportPaths(sourceRoot);
  if (!paths.length) throw new Error(`No report.json files found under ${sourceRoot}.`);

  const loaded = paths.map((path) => {
    let report: Report;
    const bytes = readFileSync(path);
    try {
      report = JSON.parse(bytes.toString('utf8')) as Report;
    } catch (error) {
      throw new Error(`${path}: report is not valid JSON.`, { cause: error });
    }
    const schemaErrors = validateSchema(report);
    if (schemaErrors.length) throw new Error(`${path}: schema validation failed: ${schemaErrors.join('; ')}`);
    const semanticErrors = validateBundle(report);
    if (semanticErrors.length) throw new Error(`${path}: semantic validation failed: ${semanticErrors.join('; ')}`);
    if (options.requirePublicSafe && !isStaticPublicBundle(report)) throw new Error(`${path}: public-safe catalog refuses a legacy or omniscient_research bundle.`);
    const id = options.experimentCatalogIds && report.source.experiment_id !== 'luna1912-1018-r2'
      ? `${report.source.experiment_id}--${report.source.game_id}` : report.source.game_id as string;
    const releasedArtifacts = Array.from(new Set(Object.values(report.evidence_index ?? {})
      .filter((source: any) => source.release_context === 'postgame_released_negotiation_transcript')
      .map((source: any) => source.artifact_path as string)))
      .map((artifactPath) => {
        const artifact = resolve(dirname(path), artifactPath);
        if (!inside(dirname(path), artifact) || !existsSync(artifact) || !statSync(artifact).isFile()) throw new Error(`${path}: released transcript artifact is missing or unsafe: ${artifactPath}`);
        const expected = Object.values(report.evidence_index).find((source: any) => source.artifact_path === artifactPath) as any;
        const bytes = readFileSync(artifact);
        if (createHash('sha256').update(bytes).digest('hex') !== expected.sha256) throw new Error(`${path}: released transcript artifact checksum mismatch: ${artifactPath}`);
        return { artifactPath, artifact };
      });
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    const entry = catalogEntryFromReport(id, report, { fixture: options.fixture, sha256 });
    if (options.experimentCatalogIds) entry.label = report.source.experiment_id === 'luna1912-1018-r2' ? 'Luna 1912' : report.game.title;
    return { path, bytes, id, report, releasedArtifacts, entry, narrativePath: undefined as string | undefined };
  }).sort((left, right) => left.id.localeCompare(right.id));

  if (options.approvedNarrativeRoot) {
    const narrativeRoot = resolve(options.approvedNarrativeRoot);
    if (!existsSync(narrativeRoot) || !statSync(narrativeRoot).isDirectory()) throw new Error(`Approved narrative root does not exist or is not a directory: ${narrativeRoot}`);
    for (const item of loaded) {
      if (item.entry.audience.startsWith('player-')) throw new Error(`Approved player-perspective narratives are not supported; omit --approved-narrative-root for ${item.id}.`);
      const narrativeAudience = item.entry.audience as NarrativeAudience;
      const narrativePath = join(narrativeRoot, item.id, 'approved.json');
      if (!existsSync(narrativePath)) throw new Error(`Approved narrative is missing for ${item.id}: ${narrativePath}`);
      const narrativeBytes = readFileSync(narrativePath);
      let narrative: unknown;
      try { narrative = JSON.parse(narrativeBytes.toString('utf8')); }
      catch (error) { throw new Error(`${narrativePath}: approved narrative is not valid JSON.`, { cause: error }); }
      const errors = [
        ...validateHistorianNarrativeSchema(narrative),
        ...validateHistorianNarrative(narrative, item.report, { reportSha256: item.entry.sha256, audience: narrativeAudience }),
      ];
      if (errors.length) throw new Error(`${narrativePath}: approved narrative validation failed: ${errors.join('; ')}`);
      item.entry.narrative = {
        schema_version: 'historian_narrative_v1',
        narrative_url: `narratives/${item.id}/approved.json`,
        sha256: createHash('sha256').update(narrativeBytes).digest('hex'),
        report_sha256: item.entry.sha256,
        audience: narrativeAudience,
      };
      item.narrativePath = narrativePath;
    }
  }

  const catalog = validateCatalog({ schema_version: REPORT_CATALOG_SCHEMA, reports: loaded.map(({ entry }) => entry) });
  mkdirSync(dirname(outputRoot), { recursive: true });
  const stagingRoot = mkdtempSync(join(dirname(outputRoot), '.report-catalog-'));
  try {
    for (const item of loaded) {
      const destination = join(stagingRoot, 'bundles', item.id, 'report.json');
      mkdirSync(dirname(destination), { recursive: true });
      copyFileSync(item.path, destination);
      for (const released of item.releasedArtifacts) {
        const releasedDestination = join(dirname(destination), released.artifactPath);
        mkdirSync(dirname(releasedDestination), { recursive: true });
        copyFileSync(released.artifact, releasedDestination);
      }
      if (item.narrativePath) {
        const narrativeDestination = join(stagingRoot, 'narratives', item.id, 'approved.json');
        mkdirSync(dirname(narrativeDestination), { recursive: true });
        copyFileSync(item.narrativePath, narrativeDestination);
      }
    }
    writeFileSync(join(stagingRoot, 'catalog.json'), `${JSON.stringify(catalog, null, 2)}\n`, 'utf8');
    if (existsSync(outputRoot)) {
      if (!options.force) throw new Error(`Catalog output already exists; pass --force to replace it: ${outputRoot}`);
      rmSync(outputRoot, { recursive: true, force: true });
    }
    try {
      renameSync(stagingRoot, outputRoot);
    } catch (error) {
      if (!(error instanceof Error) || !('code' in error) || error.code !== 'EPERM') throw error;
      cpSync(stagingRoot, outputRoot, { recursive: true });
      rmSync(stagingRoot, { recursive: true, force: true });
    }
  } catch (error) {
    if (existsSync(stagingRoot)) rmSync(stagingRoot, { recursive: true, force: true });
    throw error;
  }
  return catalog;
}

function option(name: string): string | undefined {
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const sourceRoot = option('--source-root');
  const outputRoot = option('--output-root') ?? 'public/reports';
  if (!sourceRoot) {
    console.error('Usage: node --experimental-strip-types scripts/build_report_catalog.ts --source-root <path> [--output-root <path>] [--approved-narrative-root <path>] [--require-public-safe] [--fixture] [--force]');
    process.exitCode = 2;
  } else {
    try {
      const catalog = buildReportCatalog({
        sourceRoot,
        outputRoot,
        fixture: process.argv.includes('--fixture'),
        force: process.argv.includes('--force'),
        approvedNarrativeRoot: option('--approved-narrative-root'),
        requirePublicSafe: process.argv.includes('--require-public-safe'),
      });
      console.log(`Built ${catalog.reports.length} report catalog entries at ${resolve(outputRoot)}.`);
    } catch (error) {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    }
  }
}
