import { createHash } from 'node:crypto';
import {
  copyFileSync,
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
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildReportCatalog } from './build_report_catalog.ts';
import {
  loadCatalog,
  loadCatalogReport,
  validateCatalog,
  type FetchLike,
  type ReportCatalogV1,
} from '../src/report-loader.ts';
import { isStaticPublicBundle, resolveBundleAudience } from '../src/report-model/audience.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import type { Report } from '../src/viewer-model.ts';

const STAGE_SCHEMA = 'diplomacy_public_stage_audit_v1' as const;
const BUILD_SCHEMA = 'diplomacy_public_build_audit_v1' as const;
const AUDIENCE_MODE = 'postgame_public' as const;
const POLICY_VERSION = 'diplomacy_audience_policy_v1' as const;
const PRIVATE_SENTINELS = ['PHASE_5B_PRIVATE_TEXT_SENTINEL'];
const REPOSITORY_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const TEXT_EXTENSIONS = new Set(['.css', '.html', '.js', '.json', '.md', '.svg', '.txt', '.xml']);
const DISALLOWED_PRIVATE_KEYS = new Set([
  'belief_summary', 'beliefs', 'campaign_plan', 'commitment_memory', 'outcome_review',
  'persistent_memory', 'private_records', 'raw_prompt_context', 'reflection', 'reflections',
  'winter_assessment',
]);

export type PublicStageAuditV1 = {
  schema_version: typeof STAGE_SCHEMA;
  audience_mode: typeof AUDIENCE_MODE;
  audience_policy_version: typeof POLICY_VERSION;
  approved_for_static_public_distribution: true;
  bundle_count: number;
  game_ids: string[];
  files: Array<{ path: string; bytes: number; sha256: string }>;
  error_count: 0;
};

export type PublicBuildAuditV1 = {
  schema_version: typeof BUILD_SCHEMA;
  audience_mode: typeof AUDIENCE_MODE;
  audience_policy_version: typeof POLICY_VERSION;
  bundle_count: number;
  file_count: number;
  artifact_bytes: number;
  phase_count: number;
  board_state_count: number;
  order_count: number;
  adjudication_count: number;
  message_count: number;
  released_message_count: number;
  evidence_count: number;
  released_private_message_evidence_count: number;
  private_record_count: number;
  omniscient_evidence_count: number;
  unresolved_reference_count: number;
  missing_catalog_asset_count: number;
  research_bundle_match_count: number;
  forbidden_id_match_count: number;
  absolute_path_count: number;
  private_sentinel_count: number;
  private_marker_count: number;
  raw_artifact_count: number;
  source_map_count: number;
  loader_error_count: number;
  errors: string[];
  error_count: number;
};

export type StagePublicReportsOptions = { sourceRoot: string; outputRoot: string };
export type VerifyPublicBuildOptions = { distRoot: string; forbiddenSourceRoot?: string; auditPath?: string };

const hash = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
const object = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const inside = (parent: string, child: string) => {
  const path = relative(resolve(parent), resolve(child));
  return path === '' || (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path));
};
const portable = (root: string, path: string) => relative(root, path).replaceAll('\\', '/');
const absoluteLocalPaths = (text: string) => [
  ...text.matchAll(/(?:^|[\s"'])((?:[A-Za-z]:[\\/][^\s"']+|\\\\[A-Za-z0-9._-]+\\[A-Za-z0-9$._-]+(?:\\[^\s"']*)?))/gm),
  ...text.matchAll(/(?:^|[\s"'])((?:\/Users|\/home|\/tmp|\/var)\/[A-Za-z0-9._-]+\/[^\s"']*)/gm),
].map((match) => match[1]);

function files(root: string): string[] {
  const found: string[] = [];
  const visit = (directory: string) => {
    for (const item of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(directory, item.name);
      if (item.isDirectory()) visit(path);
      else if (item.isFile()) found.push(path);
    }
  };
  visit(root);
  return found.sort((left, right) => portable(root, left).localeCompare(portable(root, right)));
}

function reportFiles(root: string): string[] {
  return files(root).filter((path) => path.endsWith(`${sep}report.json`) || portable(root, path) === 'report.json');
}

function assertSafeSource(sourceRoot: string, outputRoot: string) {
  if (!existsSync(sourceRoot) || !statSync(sourceRoot).isDirectory()) throw new Error(`Public projection source does not exist or is not a directory: ${sourceRoot}`);
  if (inside(sourceRoot, outputRoot) || inside(outputRoot, sourceRoot)) throw new Error('Public source and staging output must not overlap.');
  if ([sourceRoot, outputRoot].some((path) => resolve(path).split(/[\\/]+/).some((part) => part.toLowerCase() === 'experiments'))) {
    throw new Error('Raw experiment paths are not valid public-build inputs or outputs; use a Phase 5A postgame_public projection root.');
  }
  if (resolve(outputRoot) === resolve(REPOSITORY_ROOT, 'public', 'reports')) {
    throw new Error('Public-build staging must not replace the local omniscient research catalog under public/reports.');
  }
}

function validateOptionalSourceCatalog(sourceRoot: string) {
  const path = join(sourceRoot, 'catalog.json');
  if (!existsSync(path)) return;
  let catalog: ReportCatalogV1;
  try { catalog = validateCatalog(JSON.parse(readFileSync(path, 'utf8'))); }
  catch (error) { throw new Error(`Source catalog validation failed: ${error instanceof Error ? error.message : String(error)}`); }
  for (const entry of catalog.reports) {
    if (entry.audience_mode !== AUDIENCE_MODE || entry.audience_policy_version !== POLICY_VERSION || entry.approved_for_static_public_distribution !== true) {
      throw new Error(`Source catalog report ${entry.id} is not explicitly postgame_public and approved.`);
    }
    const reportPath = resolve(dirname(path), entry.report_url);
    if (!inside(sourceRoot, reportPath) || !existsSync(reportPath) || !statSync(reportPath).isFile()) {
      throw new Error(`Source catalog report ${entry.id} has an unsafe or unavailable report_url.`);
    }
    const report = JSON.parse(readFileSync(reportPath, 'utf8')) as Report;
    if (report.bundle_id !== entry.bundle_id || report.source?.game_id !== (entry.source_game_id ?? entry.id)) {
      throw new Error(`Source catalog/report identity mismatch for ${entry.id}.`);
    }
    if (!isStaticPublicBundle(report)) throw new Error(`Source catalog report ${entry.id} is not public-safe.`);
  }
}

function copyMapAssets(destinationRoot: string) {
  const source = resolve(REPOSITORY_ROOT, 'public', 'maps');
  const destination = join(destinationRoot, 'maps');
  mkdirSync(destination, { recursive: true });
  for (const path of files(source)) {
    const target = join(destination, relative(source, path));
    mkdirSync(dirname(target), { recursive: true });
    copyFileSync(path, target);
  }
}

function publishDirectory(staging: string, destination: string) {
  try { renameSync(staging, destination); }
  catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'EPERM') throw error;
    throw new Error(`Atomic publication is unavailable for ${destination}; staging was retained for diagnosis.`, { cause: error });
  }
}

export function stagePublicReports(options: StagePublicReportsOptions): PublicStageAuditV1 {
  const sourceRoot = resolve(options.sourceRoot);
  const outputRoot = resolve(options.outputRoot);
  assertSafeSource(sourceRoot, outputRoot);
  if (existsSync(outputRoot)) throw new Error(`Public staging destination already exists; refusing to overwrite: ${outputRoot}`);
  validateOptionalSourceCatalog(sourceRoot);
  mkdirSync(dirname(outputRoot), { recursive: true });
  const staging = mkdtempSync(join(dirname(outputRoot), '.public-build-stage-'));
  try {
    const catalog = buildReportCatalog({
      sourceRoot,
      outputRoot: join(staging, 'reports'),
      requirePublicSafe: true,
    });
    if (catalog.reports.some((entry) => entry.audience_mode !== AUDIENCE_MODE
      || entry.audience_policy_version !== POLICY_VERSION
      || entry.approved_for_static_public_distribution !== true)) {
      throw new Error('Generated public catalog contains a non-public-safe or mixed-audience entry.');
    }
    copyMapAssets(staging);
    const stagedFiles = files(staging).map((path) => {
      const bytes = readFileSync(path);
      return { path: portable(staging, path), bytes: bytes.length, sha256: hash(bytes) };
    });
    const audit: PublicStageAuditV1 = {
      schema_version: STAGE_SCHEMA,
      audience_mode: AUDIENCE_MODE,
      audience_policy_version: POLICY_VERSION,
      approved_for_static_public_distribution: true,
      bundle_count: catalog.reports.length,
      game_ids: catalog.reports.map((entry) => entry.id),
      files: stagedFiles,
      error_count: 0,
    };
    writeFileSync(join(staging, 'public-build-audit.json'), json(audit), 'utf8');
    publishDirectory(staging, outputRoot);
    return audit;
  } catch (error) {
    if (existsSync(staging)) rmSync(staging, { recursive: true, force: true });
    throw error;
  }
}

function collectForbiddenIds(sourceRoot?: string): { ids: Set<string>; researchHashes: Set<string> } {
  const ids = new Set<string>();
  const researchHashes = new Set<string>();
  if (!sourceRoot) return { ids, researchHashes };
  const root = resolve(sourceRoot);
  if (!existsSync(root)) return { ids, researchHashes };
  for (const path of reportFiles(root)) {
    const bytes = readFileSync(path);
    researchHashes.add(hash(bytes));
    let report: any;
    try { report = JSON.parse(bytes.toString('utf8')); } catch { continue; }
    for (const [id, record] of Object.entries(report.private_records ?? {}) as Array<[string, any]>) {
      ids.add(id);
      if (typeof record?.source_ref === 'string') ids.add(record.source_ref);
    }
    for (const [id, evidence] of Object.entries(report.evidence_index ?? {}) as Array<[string, any]>) {
      if (evidence?.kind === 'private_record') ids.add(id);
    }
  }
  return { ids, researchHashes };
}

function structuralPrivateMarkers(value: unknown, path = ''): string[] {
  const found: string[] = [];
  if (Array.isArray(value)) value.forEach((item, index) => found.push(...structuralPrivateMarkers(item, `${path}/${index}`)));
  else if (object(value)) {
    for (const [key, item] of Object.entries(value)) {
      const next = `${path}/${key}`;
      if (key !== 'private_records' && DISALLOWED_PRIVATE_KEYS.has(key)) found.push(next);
      if (key === 'record_type' && typeof item === 'string' && DISALLOWED_PRIVATE_KEYS.has(item)) found.push(next);
      found.push(...structuralPrivateMarkers(item, next));
    }
  }
  return found;
}

function unresolvedEvidenceReferences(report: Report): string[] {
  const evidence = report.evidence_index ?? {};
  const unresolved: string[] = [];
  const visit = (value: unknown, path = '') => {
    if (Array.isArray(value)) value.forEach((item, index) => visit(item, `${path}/${index}`));
    else if (object(value)) for (const [key, item] of Object.entries(value)) {
      const next = `${path}/${key}`;
      if ((key === 'source_ref' || key.endsWith('_source_ref')) && typeof item === 'string' && !evidence[item]) unresolved.push(next);
      if (key === 'source_refs' && Array.isArray(item)) item.forEach((id, index) => { if (typeof id === 'string' && !evidence[id]) unresolved.push(`${next}/${index}`); });
      visit(item, next);
    }
  };
  visit(report);
  return unresolved;
}

function fileFetcher(distRoot: string): FetchLike {
  return async (url) => {
    const parsed = new URL(url);
    const path = resolve(distRoot, ...decodeURIComponent(parsed.pathname).replace(/^\/+/, '').split('/'));
    if (!inside(distRoot, path) || !existsSync(path) || !statSync(path).isFile()) {
      return { ok: false, status: 404, statusText: 'Not Found', text: async () => '' };
    }
    const text = readFileSync(path, 'utf8');
    return { ok: true, status: 200, statusText: 'OK', text: async () => text };
  };
}

export async function verifyPublicBuild(options: VerifyPublicBuildOptions): Promise<PublicBuildAuditV1> {
  const distRoot = resolve(options.distRoot);
  const errors: string[] = [];
  const allFiles = existsSync(distRoot) && statSync(distRoot).isDirectory() ? files(distRoot) : [];
  if (!allFiles.length) errors.push(`Public build does not exist or is empty: ${distRoot}`);
  const relativePaths = allFiles.map((path) => portable(distRoot, path));
  const fileTexts = new Map<string, string>();
  let absolutePathCount = 0;
  let privateSentinelCount = 0;
  for (const path of allFiles) {
    if (!TEXT_EXTENSIONS.has(extname(path).toLowerCase())) continue;
    const text = readFileSync(path, 'utf8');
    fileTexts.set(path, text);
    absolutePathCount += absoluteLocalPaths(text).length;
    privateSentinelCount += PRIVATE_SENTINELS.reduce((count, sentinel) => count + (text.includes(sentinel) ? 1 : 0), 0);
  }
  if (absolutePathCount) errors.push(`Found ${absolutePathCount} absolute local path occurrence(s).`);
  if (privateSentinelCount) errors.push(`Found ${privateSentinelCount} controlled private-text sentinel occurrence(s).`);

  const rawArtifactPaths = relativePaths.filter((path) => /(?:^|\/)(?:experiments|agents|raw_artifacts|fixtures)(?:\/|$)/i.test(path));
  const sourceMaps = relativePaths.filter((path) => path.endsWith('.map'));
  if (rawArtifactPaths.length) errors.push(`Found ${rawArtifactPaths.length} raw/research artifact path(s).`);
  if (sourceMaps.length) errors.push(`Found ${sourceMaps.length} source map(s).`);

  const { ids: forbiddenIds, researchHashes } = collectForbiddenIds(options.forbiddenSourceRoot);
  let forbiddenIdMatches = 0;
  for (const text of fileTexts.values()) for (const id of forbiddenIds) if (text.includes(id)) forbiddenIdMatches += 1;
  const researchBundleMatches = allFiles.reduce((count, path) => count + (researchHashes.has(hash(readFileSync(path))) ? 1 : 0), 0);
  if (forbiddenIdMatches) errors.push(`Found ${forbiddenIdMatches} private-only source ID occurrence(s).`);
  if (researchBundleMatches) errors.push(`Found ${researchBundleMatches} byte-identical omniscient research bundle(s).`);

  let catalog: ReportCatalogV1 = { schema_version: 'diplomacy_report_catalog_v1', reports: [] };
  const catalogPath = join(distRoot, 'reports', 'catalog.json');
  try {
    catalog = validateCatalog(JSON.parse(readFileSync(catalogPath, 'utf8')));
  } catch (error) { errors.push(`Public catalog failed validation: ${error instanceof Error ? error.message : String(error)}`); }

  let phaseCount = 0;
  let boardStateCount = 0;
  let orderCount = 0;
  let adjudicationCount = 0;
  let messageCount = 0;
  let releasedMessageCount = 0;
  let evidenceCount = 0;
  let releasedPrivateMessageEvidenceCount = 0;
  let privateRecordCount = 0;
  let omniscientEvidenceCount = 0;
  let unresolvedReferenceCount = 0;
  let missingCatalogAssetCount = 0;
  let privateMarkerCount = 0;
  let loaderErrorCount = 0;
  const fetcher = fileFetcher(distRoot);
  let loadedCatalogUrl = 'https://public-build.invalid/reports/catalog.json';
  try {
    const loaded = await loadCatalog(loadedCatalogUrl, fetcher);
    loadedCatalogUrl = loaded.url;
  } catch (error) {
    loaderErrorCount += 1;
    errors.push(`Report loader could not load the emitted catalog: ${error instanceof Error ? error.message : String(error)}`);
  }
  for (const entry of catalog.reports) {
    if (entry.audience_mode !== AUDIENCE_MODE || entry.audience_policy_version !== POLICY_VERSION
      || entry.approved_for_static_public_distribution !== true || entry.audience !== 'public') {
      errors.push(`${entry.id}: catalog audience metadata is not approved postgame_public.`);
    }
    const reportPath = resolve(dirname(catalogPath), entry.report_url);
    if (!inside(join(distRoot, 'reports'), reportPath) || !existsSync(reportPath)) {
      missingCatalogAssetCount += 1;
      errors.push(`${entry.id}: catalog report URL does not resolve inside dist-public.`);
      continue;
    }
    let report: Report;
    try {
      report = JSON.parse(readFileSync(reportPath, 'utf8')) as Report;
      const validation = [...validateSchema(report), ...validateBundle(report)];
      if (validation.length) throw new Error(validation.join('; '));
      const audience = resolveBundleAudience(report);
      if (!isStaticPublicBundle(report) || audience.mode !== AUDIENCE_MODE || audience.policy_version !== POLICY_VERSION) throw new Error('bundle is not explicitly approved postgame_public');
      await loadCatalogReport(entry, loadedCatalogUrl, fetcher);
    } catch (error) {
      loaderErrorCount += 1;
      errors.push(`${entry.id}: emitted report failed validation/loading: ${error instanceof Error ? error.message : String(error)}`);
      continue;
    }
    privateRecordCount += Object.keys(report.private_records ?? {}).length;
    privateMarkerCount += structuralPrivateMarkers(report).length;
    const unresolved = unresolvedEvidenceReferences(report);
    unresolvedReferenceCount += unresolved.length;
    phaseCount += (report.years ?? []).flatMap((year: any) => year.phases ?? []).length;
    for (const phase of (report.years ?? []).flatMap((year: any) => year.phases ?? [])) {
      boardStateCount += Number(Boolean(phase.board_before)) + Number(Boolean(phase.board_after));
      if (phase.adjudication !== undefined) adjudicationCount += 1;
    }
    orderCount += Object.keys(report.orders ?? {}).length;
    messageCount += Object.keys(report.messages ?? {}).length;
    releasedMessageCount += Object.values(report.messages ?? {}).filter((message: any) => message.visibility === 'private').length;
    evidenceCount += Object.keys(report.evidence_index ?? {}).length;
    omniscientEvidenceCount += Object.values(report.evidence_index ?? {}).filter((source: any) => source.visibility === 'omniscient_postgame').length;
    const releasedArtifacts = new Map<string, string>();
    for (const source of Object.values(report.evidence_index ?? {}) as any[]) {
      if (source.visibility === 'private') {
        if (source.kind === 'message' && source.release_context === 'postgame_released_negotiation_transcript') releasedPrivateMessageEvidenceCount += 1;
        else errors.push(`${entry.id}: non-released private evidence remains.`);
      }
      if (source.release_context === 'postgame_released_negotiation_transcript') releasedArtifacts.set(source.artifact_path, source.sha256);
    }
    for (const [artifactPath, expectedHash] of releasedArtifacts) {
      const artifact = resolve(dirname(reportPath), artifactPath);
      if (!inside(dirname(reportPath), artifact) || !existsSync(artifact)) {
        missingCatalogAssetCount += 1;
        errors.push(`${entry.id}: released transcript companion is missing or unsafe.`);
      } else if (hash(readFileSync(artifact)) !== expectedHash) errors.push(`${entry.id}: released transcript companion checksum mismatch.`);
    }
  }
  if (privateRecordCount) errors.push(`Found ${privateRecordCount} private record(s).`);
  if (privateMarkerCount) errors.push(`Found ${privateMarkerCount} private-only structural marker(s).`);
  if (omniscientEvidenceCount) errors.push(`Found ${omniscientEvidenceCount} omniscient evidence record(s).`);
  if (unresolvedReferenceCount) errors.push(`Found ${unresolvedReferenceCount} unresolved evidence reference(s).`);
  if (catalog.reports.length && (!phaseCount || !boardStateCount || !orderCount || !adjudicationCount)) {
    errors.push('Expected public phases, map state, orders, and adjudications were not all represented.');
  }

  const audit: PublicBuildAuditV1 = {
    schema_version: BUILD_SCHEMA,
    audience_mode: AUDIENCE_MODE,
    audience_policy_version: POLICY_VERSION,
    bundle_count: catalog.reports.length,
    file_count: allFiles.length,
    artifact_bytes: allFiles.reduce((sum, path) => sum + statSync(path).size, 0),
    phase_count: phaseCount,
    board_state_count: boardStateCount,
    order_count: orderCount,
    adjudication_count: adjudicationCount,
    message_count: messageCount,
    released_message_count: releasedMessageCount,
    evidence_count: evidenceCount,
    released_private_message_evidence_count: releasedPrivateMessageEvidenceCount,
    private_record_count: privateRecordCount,
    omniscient_evidence_count: omniscientEvidenceCount,
    unresolved_reference_count: unresolvedReferenceCount,
    missing_catalog_asset_count: missingCatalogAssetCount,
    research_bundle_match_count: researchBundleMatches,
    forbidden_id_match_count: forbiddenIdMatches,
    absolute_path_count: absolutePathCount,
    private_sentinel_count: privateSentinelCount,
    private_marker_count: privateMarkerCount,
    raw_artifact_count: rawArtifactPaths.length,
    source_map_count: sourceMaps.length,
    loader_error_count: loaderErrorCount,
    errors,
    error_count: errors.length,
  };
  if (options.auditPath) {
    const auditPath = resolve(options.auditPath);
    mkdirSync(dirname(auditPath), { recursive: true });
    writeFileSync(auditPath, json(audit), 'utf8');
  }
  return audit;
}

function option(name: string): string | undefined {
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const command = process.argv[2];
  try {
    if (command === 'stage') {
      const sourceRoot = option('--source-root');
      if (!sourceRoot) throw new Error('Usage: public_build.ts stage --source-root <phase5a-root> [--output-root public-build-input]');
      const audit = stagePublicReports({ sourceRoot, outputRoot: option('--output-root') ?? 'public-build-input' });
      console.log(json(audit).trimEnd());
    } else if (command === 'verify-stage') {
      const inputRoot = option('--input-root') ?? process.env.PUBLIC_BUILD_INPUT_ROOT ?? 'public-build-input';
      const audit = await verifyPublicBuild({ distRoot: inputRoot });
      if (audit.error_count) throw new Error(`Public stage verification failed: ${audit.errors.join('; ')}`);
      console.log(`Verified ${audit.bundle_count} public staging bundle(s).`);
    } else if (command === 'verify-build') {
      const audit = await verifyPublicBuild({
        distRoot: option('--dist-root') ?? 'dist-public',
        forbiddenSourceRoot: option('--forbidden-source-root') ?? 'fixtures/phase4a_generated',
        auditPath: option('--audit-path') ?? 'public-build-audits/verification-audit.json',
      });
      console.log(json(audit).trimEnd());
      if (audit.error_count) process.exitCode = 1;
    } else throw new Error('Usage: public_build.ts <stage|verify-stage|verify-build> [options]');
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
