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
import { build as viteBuild } from 'vite';
import { validateCatalog, type ReportCatalogEntry, type ReportCatalogV1 } from '../src/report-loader.ts';
import { validateHistorianNarrative, validateHistorianNarrativeSchema, type NarrativeAudience } from '../src/narrative-model.ts';
import { audienceAgentId, isReportAudience, type ReportAudience } from '../src/audience-projection.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import type { Report } from '../src/viewer-model.ts';

export type ArchiveManifestV1 = {
  schema_version: 'diplomacy_archive_manifest_v1';
  package_id: string;
  audience: ReportAudience;
  report_ids: string[];
  catalog_sha256: string;
  files: Array<{ path: string; bytes: number; sha256: string }>;
};

export type PackageReportOptions = {
  catalogPath: string;
  outputRoot: string;
  audience: ReportAudience;
  reportIds?: string[];
  force?: boolean;
};

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hash = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
const inside = (parent: string, child: string) => {
  const path = relative(resolve(parent), resolve(child));
  return path === '' || (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path));
};
const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;

function sourcePath(base: string, relativeUrl: string): string {
  const path = resolve(base, ...relativeUrl.split('/'));
  if (!inside(base, path)) throw new Error(`Catalog reference escapes its directory: ${relativeUrl}`);
  return path;
}

function files(root: string): string[] {
  const found: string[] = [];
  const visit = (directory: string) => {
    for (const item of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(directory, item.name);
      if (item.isDirectory()) visit(path);
      else if (item.isFile()) found.push(relative(root, path).replaceAll('\\', '/'));
    }
  };
  visit(root);
  return found.sort();
}

function validatePublicReport(report: Report, entry: ReportCatalogEntry): string[] {
  const errors: string[] = [];
  if (entry.audience !== 'public' || report.source?.visibility_mode !== 'public') errors.push('public package cannot include an omniscient or player-limited report');
  if (Object.keys(report.private_records ?? {}).length) errors.push('public report contains private records');
  for (const [id, source] of Object.entries(report.evidence_index ?? {}) as Array<[string, any]>) if (source.visibility !== 'public') errors.push(`public report contains non-public evidence ${id}`);
  for (const [id, message] of Object.entries(report.messages ?? {}) as Array<[string, any]>) if (message.visibility !== 'public') errors.push(`public report contains non-public message ${id}`);
  return errors;
}

function validatePlayerReport(report: Report, entry: ReportCatalogEntry, audience: ReportAudience): string[] {
  const errors: string[] = [];
  const agentId = audienceAgentId(audience);
  if (!agentId || entry.audience !== audience || report.source?.visibility_mode !== audience) errors.push('player package audience does not match its catalog/report');
  if (!report.roster.some((member: any) => member.agent_id === agentId)) errors.push('player package agent is absent from the roster');
  for (const [id, record] of Object.entries(report.private_records ?? {}) as Array<[string, any]>) if (record.agent_id !== agentId) errors.push(`player package contains another player’s record ${id}`);
  for (const [id, message] of Object.entries(report.messages ?? {}) as Array<[string, any]>) if (message.visibility !== 'public' && message.sender_agent_id !== agentId && !message.recipient_agent_ids.includes(agentId)) errors.push(`player package contains another player’s message ${id}`);
  for (const [id, source] of Object.entries(report.evidence_index ?? {}) as Array<[string, any]>) if (source.visibility === 'omniscient_postgame') errors.push(`player package contains omniscient evidence ${id}`);
  if (entry.narrative) errors.push('player package cannot include a narrative overlay');
  return errors;
}

function replaceDirectory(staging: string, destination: string, force: boolean) {
  if (existsSync(destination)) {
    if (!force) throw new Error(`Package destination already exists; pass --force to replace it: ${destination}`);
    rmSync(destination, { recursive: true, force: true });
  }
  try { renameSync(staging, destination); }
  catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'EPERM') throw error;
    cpSync(staging, destination, { recursive: true });
    rmSync(staging, { recursive: true, force: true });
  }
}

export function verifyPackageDirectory(rootInput: string): string[] {
  const root = resolve(rootInput);
  const errors: string[] = [];
  let manifest: ArchiveManifestV1;
  try { manifest = JSON.parse(readFileSync(join(root, 'package-manifest.json'), 'utf8')) as ArchiveManifestV1; }
  catch (error) { return [`package-manifest.json is missing or invalid: ${error instanceof Error ? error.message : error}`]; }
  if (manifest.schema_version !== 'diplomacy_archive_manifest_v1' || !Array.isArray(manifest.files)) return ['package manifest has unsupported schema'];
  const expected = new Set<string>();
  for (const item of manifest.files) {
    if (!item.path || item.path.includes('\\') || item.path.startsWith('/') || item.path.split('/').includes('..')) { errors.push(`unsafe manifest path ${item.path}`); continue; }
    expected.add(item.path);
    const path = join(root, ...item.path.split('/'));
    if (!existsSync(path) || !statSync(path).isFile()) { errors.push(`missing manifested file ${item.path}`); continue; }
    const bytes = readFileSync(path);
    if (bytes.length !== item.bytes) errors.push(`size mismatch ${item.path}`);
    if (hash(bytes) !== item.sha256) errors.push(`checksum mismatch ${item.path}`);
  }
  const actual = files(root).filter((path) => path !== 'package-manifest.json');
  for (const path of actual) if (!expected.has(path)) errors.push(`unmanifested file ${path}`);
  for (const path of expected) if (!actual.includes(path)) errors.push(`manifest file absent ${path}`);
  return errors;
}

export async function packageReport(options: PackageReportOptions): Promise<{ manifest: ArchiveManifestV1; destination: string }> {
  const catalogPath = resolve(options.catalogPath);
  const catalogBase = dirname(catalogPath);
  const outputRoot = resolve(options.outputRoot);
  if (!existsSync(catalogPath)) throw new Error(`Catalog does not exist: ${catalogPath}`);
  const catalog = validateCatalog(JSON.parse(readFileSync(catalogPath, 'utf8')));
  const requested = options.reportIds?.length ? new Set(options.reportIds) : null;
  const selected = catalog.reports.filter((entry) => !requested || requested.has(entry.id)).sort((a, b) => a.id.localeCompare(b.id));
  if (!selected.length) throw new Error('Package selection contains no reports.');
  if (requested) for (const id of requested) if (!selected.some((entry) => entry.id === id)) throw new Error(`Requested report is absent from catalog: ${id}`);
  for (const entry of selected) if (entry.audience !== options.audience) throw new Error(`Package audience ${options.audience} does not match ${entry.id} audience ${entry.audience}.`);

  const loaded = selected.map((entry) => {
    const reportPath = sourcePath(catalogBase, entry.report_url);
    const reportBytes = readFileSync(reportPath);
    if (hash(reportBytes) !== entry.sha256) throw new Error(`${entry.id}: report checksum mismatch.`);
    const report = JSON.parse(reportBytes.toString('utf8')) as Report;
    const errors = [...validateSchema(report), ...validateBundle(report)];
    if (report.bundle_id !== entry.bundle_id || report.source?.game_id !== (entry.source_game_id ?? entry.id)) errors.push('catalog/report identity mismatch');
    if (options.audience === 'public') errors.push(...validatePublicReport(report, entry));
    if (options.audience.startsWith('player-')) errors.push(...validatePlayerReport(report, entry, options.audience));
    let narrativePath: string | undefined;
    if (entry.narrative) {
      narrativePath = sourcePath(catalogBase, entry.narrative.narrative_url);
      const narrativeBytes = readFileSync(narrativePath);
      if (hash(narrativeBytes) !== entry.narrative.sha256) errors.push('narrative checksum mismatch');
      else {
        const narrative = JSON.parse(narrativeBytes.toString('utf8'));
        if (options.audience.startsWith('player-')) errors.push('player package cannot include a narrative overlay');
        else errors.push(...validateHistorianNarrativeSchema(narrative), ...validateHistorianNarrative(narrative, report, { reportSha256: entry.sha256, audience: options.audience as NarrativeAudience }));
      }
    }
    if (errors.length) throw new Error(`${entry.id}: package input validation failed: ${errors.join('; ')}`);
    return { entry, report, reportPath, narrativePath };
  });

  mkdirSync(dirname(outputRoot), { recursive: true });
  const staging = mkdtempSync(join(dirname(outputRoot), '.report-package-'));
  try {
    const stagedPublic = join(staging, '_selected-public');
    const stagedReports = join(stagedPublic, 'reports');
    mkdirSync(stagedReports, { recursive: true });
    const maps = join(repositoryRoot, 'public', 'maps');
    if (existsSync(maps)) cpSync(maps, join(stagedPublic, 'maps'), { recursive: true });
    const packagedCatalog: ReportCatalogV1 = { schema_version: 'diplomacy_report_catalog_v1', reports: selected };
    const catalogText = json(packagedCatalog);
    writeFileSync(join(stagedReports, 'catalog.json'), catalogText, 'utf8');
    for (const item of loaded) {
      const reportDestination = join(stagedReports, ...item.entry.report_url.split('/'));
      mkdirSync(dirname(reportDestination), { recursive: true });
      copyFileSync(item.reportPath, reportDestination);
      if (item.narrativePath && item.entry.narrative) {
        const narrativeDestination = join(stagedReports, ...item.entry.narrative.narrative_url.split('/'));
        mkdirSync(dirname(narrativeDestination), { recursive: true });
        copyFileSync(item.narrativePath, narrativeDestination);
      }
    }
    await viteBuild({
      root: repositoryRoot,
      base: './',
      publicDir: stagedPublic,
      logLevel: 'silent',
      build: { outDir: join(staging, 'site'), emptyOutDir: true },
    });
    rmSync(stagedPublic, { recursive: true, force: true });
    const audienceNotice = options.audience === 'public'
      ? 'This package contains only the validated public projection selected by its catalog.'
      : options.audience.startsWith('player-')
        ? `This package contains the ${options.audience.slice('player-'.length)} reading perspective, including only that player’s eligible private messages and records. It is a separate data artifact, not authentication, and must not be published as public.`
        : 'This package is an omniscient post-game archive and may contain private messages and reflections. Do not publish it as a public bundle.';
    const mapRows = [...new Map(loaded.map(({ report }) => [`${report.map.map_id}:${report.map.geometry_version}`, report.map])).values()]
      .map((map) => `- \`${map.map_id}\` / \`${map.geometry_version}\`: code-native analytical anchor geometry; no raster or third-party base-map asset is packaged.`)
      .join('\n');
    const reportRows = loaded.map(({ entry, report }) => {
      const provenance = (report.game as any).model_provenance;
      const model = provenance
        ? `${provenance.provider_name ?? 'not recorded'} / ${provenance.requested_model ?? 'not recorded'}; resolved model ${provenance.resolved_model ?? 'not recorded'}`
        : 'not included in this audience bundle';
      return `- \`${entry.id}\`: \`${entry.bundle_id}\`; experiment \`${report.source.experiment_id}\`; schema \`${report.schema_version}\`; SHA-256 \`${entry.sha256}\`; model provenance ${model}.`;
    }).join('\n');
    writeFileSync(join(staging, 'README.md'), `# Diplomacy Viewer local archive\n\n${audienceNotice}\n\n## Serve locally\n\nServe this archive over HTTP; opening \`site/index.html\` with \`file://\` is not supported.\n\n\`\`\`text\npython -m http.server 8000 --directory site\n\`\`\`\n\nThen open \`http://127.0.0.1:8000/\`.\n\n## Integrity verification\n\nFrom a checkout of the viewer repository, verify the ZIP into a new destination before serving it:\n\n\`\`\`text\nnpm run verify:archive -- --archive <archive.zip> --extract-to <new-empty-destination>\n\`\`\`\n\nVerification rejects unsafe paths, duplicate members, symlinks, manifest closure errors, and size or SHA-256 mismatches. See \`package-manifest.json\` for the complete file inventory.\n\n## Reproducibility records\n\nRead \`ATTRIBUTION.md\`, \`LICENSE-INVENTORY.md\`, and \`EVIDENCE-AUDIT-CHECKLIST.md\` before redistribution.\n`, 'utf8');
    writeFileSync(join(staging, 'ATTRIBUTION.md'), `# Attribution and provenance\n\nViewer: \`llm-diplomacy-viewer\`.\n\nAudience: \`${options.audience}\`.\n\nReport contract: \`diplomacy_report_bundle_v1\`. Catalog contract: \`diplomacy_report_catalog_v1\`.\n\n## Reports\n\n${reportRows}\n\n## Map attribution\n\n${mapRows}\n\n## Narrative and prompt provenance\n\n${selected.some((entry) => entry.narrative) ? 'Approved narrative references and their report/audience checksum bindings are recorded in `site/reports/catalog.json`.' : 'No approved narrative artifact is associated with this catalog. No narrative prompt or generated prose is included.'}\n`, 'utf8');
    writeFileSync(join(staging, 'LICENSE-INVENTORY.md'), '# License inventory\n\n| Component | Included material | License status |\n| --- | --- | --- |\n| Viewer | Compiled `llm-diplomacy-viewer` HTML/CSS/JavaScript | No repository license file was present at package time; no additional license grant is conveyed. |\n| Report data | Audience-specific exported experiment reports | Rights and redistribution authority are not encoded in the report contract; the publisher must verify them. |\n| Map | Code-native `standard_geometry_v1` analytical anchor coordinates when used | No external base-map image is packaged; publication still requires owner review of project and game-related rights. |\n| JavaScript dependencies | Code incorporated by the production bundle | Dependency licenses remain their respective authors’ licenses; this archive does not claim to be an exhaustive third-party notice bundle. |\n\nThis inventory is a reproducibility record, not legal advice or publication approval.\n', 'utf8');
    writeFileSync(join(staging, 'LICENSE-NOTICE.txt'), 'No repository license file was present when this local archive was built. This package conveys no additional license grant.\n', 'utf8');
    copyFileSync(join(repositoryRoot, 'docs', 'reproducibility', 'v1', 'EVIDENCE_AUDIT_CHECKLIST.md'), join(staging, 'EVIDENCE-AUDIT-CHECKLIST.md'));
    const manifestFiles = files(staging).map((path) => {
      const bytes = readFileSync(join(staging, ...path.split('/')));
      return { path, bytes: bytes.length, sha256: hash(bytes) };
    });
    const catalogSha256 = hash(catalogText);
    const manifest: ArchiveManifestV1 = {
      schema_version: 'diplomacy_archive_manifest_v1',
      package_id: `diplomacy-viewer:${options.audience}:${hash(`${catalogSha256}:${selected.map((entry) => entry.id).join(',')}`).slice(0, 16)}`,
      audience: options.audience,
      report_ids: selected.map((entry) => entry.id),
      catalog_sha256: catalogSha256,
      files: manifestFiles,
    };
    writeFileSync(join(staging, 'package-manifest.json'), json(manifest), 'utf8');
    const verification = verifyPackageDirectory(staging);
    if (verification.length) throw new Error(`Staged package verification failed: ${verification.join('; ')}`);
    replaceDirectory(staging, outputRoot, options.force ?? false);
    return { manifest, destination: outputRoot };
  } catch (error) {
    if (existsSync(staging)) rmSync(staging, { recursive: true, force: true });
    throw error;
  }
}

function option(name: string): string | undefined {
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const catalogPath = option('--catalog');
  const outputRoot = option('--output-root');
  const audience = option('--audience') as ReportAudience | undefined;
  const reportIds = option('--report-ids')?.split(',').filter(Boolean);
  if (!catalogPath || !outputRoot || !isReportAudience(audience)) {
    console.error('Usage: node --experimental-strip-types scripts/package_report.ts --catalog <catalog.json> --output-root <directory> --audience <public|player-agent-id|omniscient_postgame> [--report-ids game_001,game_002] [--force]');
    process.exitCode = 2;
  } else {
    packageReport({ catalogPath, outputRoot, audience: audience!, reportIds, force: process.argv.includes('--force') })
      .then(({ manifest, destination }) => console.log(`Packaged ${manifest.report_ids.join(', ')} at ${destination}.`))
      .catch((error) => { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; });
  }
}
