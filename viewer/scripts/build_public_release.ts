import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import {
  cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync,
  renameSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildAudienceProjections } from './build_audience_projections.ts';
import { packageReport, verifyPackageDirectory } from './package_report.ts';
import { validateCatalog } from '../src/report-loader.ts';

export type PublicReleaseManifestV1 = {
  schema_version: 'diplomacy_public_release_manifest_v1';
  audience: 'public';
  hosting_status: 'local_only';
  publication_approved: false;
  base_path: './';
  report_ids: string[];
  source_catalog_sha256: string;
  public_catalog_sha256: string;
  package_manifest_sha256: string;
  archive: { path: 'site.zip'; bytes: number; sha256: string };
  reports: Array<{ id: string; sha256: string }>;
  files: Array<{ path: string; bytes: number; sha256: string }>;
};

export type BuildPublicReleaseOptions = {
  catalogPath: string;
  outputRoot: string;
  reportIds?: string[];
  force?: boolean;
};

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hash = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
const safeRelative = (path: string) => Boolean(path) && !path.includes('\\') && !path.startsWith('/')
  && !isAbsolute(path) && !path.split('/').includes('..');

function relativeFiles(root: string): string[] {
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

function replaceDirectory(staging: string, destination: string, force: boolean) {
  if (existsSync(destination)) {
    if (!force) throw new Error(`Release destination exists; pass --force to replace it: ${destination}`);
    rmSync(destination, { recursive: true, force: true });
  }
  try { renameSync(staging, destination); }
  catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'EPERM') throw error;
    cpSync(staging, destination, { recursive: true });
    rmSync(staging, { recursive: true, force: true });
  }
}

export function verifyPublicRelease(releaseRoot: string): string[] {
  const root = resolve(releaseRoot);
  const errors: string[] = [];
  const manifestPath = join(root, 'deploy-manifest.json');
  if (!existsSync(manifestPath)) return ['missing deploy-manifest.json'];
  let manifest: PublicReleaseManifestV1;
  try { manifest = JSON.parse(readFileSync(manifestPath, 'utf8')); }
  catch { return ['deploy-manifest.json is not valid JSON']; }
  if (manifest.schema_version !== 'diplomacy_public_release_manifest_v1') errors.push('unsupported release manifest schema');
  if (manifest.audience !== 'public' || manifest.hosting_status !== 'local_only' || manifest.publication_approved !== false) errors.push('release safety status is invalid');
  if (manifest.base_path !== './') errors.push('release base path must be relative');
  const expected = new Set<string>();
  for (const item of manifest.files ?? []) {
    if (!safeRelative(item.path)) { errors.push(`unsafe manifest path ${item.path}`); continue; }
    if (expected.has(item.path)) { errors.push(`duplicate manifest path ${item.path}`); continue; }
    expected.add(item.path);
    const path = join(root, ...item.path.split('/'));
    if (!existsSync(path) || !statSync(path).isFile()) { errors.push(`missing release file ${item.path}`); continue; }
    const bytes = readFileSync(path);
    if (bytes.length !== item.bytes) errors.push(`size mismatch ${item.path}`);
    if (hash(bytes) !== item.sha256) errors.push(`checksum mismatch ${item.path}`);
  }
  const actual = relativeFiles(root).filter((path) => path !== 'deploy-manifest.json');
  for (const path of actual) if (!expected.has(path)) errors.push(`unmanifested release file ${path}`);
  for (const path of expected) if (!actual.includes(path)) errors.push(`manifested release file absent ${path}`);

  const packageRoot = join(root, 'site-package');
  if (existsSync(packageRoot)) errors.push(...verifyPackageDirectory(packageRoot).map((error) => `package: ${error}`));
  else errors.push('missing site-package');
  const archivePath = join(root, 'site.zip');
  if (!existsSync(archivePath)) errors.push('missing site.zip');
  else {
    const bytes = readFileSync(archivePath);
    if (manifest.archive?.path !== 'site.zip' || manifest.archive.bytes !== bytes.length || manifest.archive.sha256 !== hash(bytes)) errors.push('archive metadata mismatch');
  }

  const catalogPath = join(packageRoot, 'site', 'reports', 'catalog.json');
  if (existsSync(catalogPath)) {
    try {
      const catalogBytes = readFileSync(catalogPath);
      const catalog = validateCatalog(JSON.parse(catalogBytes.toString('utf8')));
      if (hash(catalogBytes) !== manifest.public_catalog_sha256) errors.push('public catalog checksum mismatch');
      if (catalog.reports.map((entry) => entry.id).join(',') !== manifest.report_ids.join(',')) errors.push('release report selection mismatch');
      const referenced = new Set<string>();
      for (const entry of catalog.reports) {
        if (entry.audience !== 'public') errors.push(`${entry.id}: non-public catalog audience`);
        const reportPath = join(packageRoot, 'site', 'reports', ...entry.report_url.split('/'));
        referenced.add(relative(join(packageRoot, 'site', 'reports'), reportPath).replaceAll('\\', '/'));
        if (!existsSync(reportPath)) { errors.push(`${entry.id}: missing public report`); continue; }
        const reportBytes = readFileSync(reportPath);
        if (hash(reportBytes) !== entry.sha256) errors.push(`${entry.id}: public report checksum mismatch`);
        const report = JSON.parse(reportBytes.toString('utf8'));
        if (report.source?.visibility_mode !== 'public') errors.push(`${entry.id}: report is not a public projection`);
        if (Object.keys(report.private_records ?? {}).length) errors.push(`${entry.id}: public report contains private records`);
        for (const message of Object.values(report.messages ?? {}) as any[]) if (message.visibility !== 'public') errors.push(`${entry.id}: public report contains private message ${message.message_id}`);
        for (const source of Object.values(report.evidence_index ?? {}) as any[]) if (source.visibility !== 'public') errors.push(`${entry.id}: public report contains non-public evidence ${source.source_id}`);
      }
      const bundlesRoot = join(packageRoot, 'site', 'reports', 'bundles');
      if (existsSync(bundlesRoot)) {
        const packagedReports = relativeFiles(bundlesRoot).filter((path) => path.endsWith('/report.json')).map((path) => `bundles/${path}`);
        for (const path of packagedReports) if (!referenced.has(path)) errors.push(`unreferenced packaged report ${path}`);
      }
    } catch (error) { errors.push(`public catalog verification failed: ${error instanceof Error ? error.message : String(error)}`); }
  } else errors.push('missing public catalog');
  return errors;
}

export async function buildPublicRelease(options: BuildPublicReleaseOptions): Promise<PublicReleaseManifestV1> {
  const catalogPath = resolve(options.catalogPath);
  const outputRoot = resolve(options.outputRoot);
  if (!existsSync(catalogPath)) throw new Error(`Source catalog does not exist: ${catalogPath}`);
  if (existsSync(outputRoot) && !options.force) throw new Error(`Release destination exists; pass --force to replace it: ${outputRoot}`);
  mkdirSync(dirname(outputRoot), { recursive: true });
  const staging = mkdtempSync(join(dirname(outputRoot), '.public-release-'));
  try {
    const projectionRoot = join(staging, '.projection');
    buildAudienceProjections({ catalogPath, outputRoot: projectionRoot, audiences: ['public'] });
    const publicCatalogPath = join(projectionRoot, 'public', 'catalog.json');
    const packageRoot = join(staging, 'site-package');
    const packaged = await packageReport({
      catalogPath: publicCatalogPath,
      outputRoot: packageRoot,
      audience: 'public',
      reportIds: options.reportIds,
    });
    const archivePath = join(staging, 'site.zip');
    const archived = spawnSync('python', [join(repositoryRoot, 'scripts', 'archive_report.py'), 'create', '--package-root', packageRoot, '--output', archivePath], { cwd: repositoryRoot, encoding: 'utf8' });
    if (archived.status !== 0) throw new Error(`Public archive creation failed: ${archived.stderr || archived.stdout}`);
    const packagedCatalogBytes = readFileSync(join(packageRoot, 'site', 'reports', 'catalog.json'));
    const packagedCatalog = validateCatalog(JSON.parse(packagedCatalogBytes.toString('utf8')));
    rmSync(projectionRoot, { recursive: true, force: true });
    const archiveBytes = readFileSync(archivePath);
    const manifest: PublicReleaseManifestV1 = {
      schema_version: 'diplomacy_public_release_manifest_v1',
      audience: 'public',
      hosting_status: 'local_only',
      publication_approved: false,
      base_path: './',
      report_ids: packaged.manifest.report_ids,
      source_catalog_sha256: hash(readFileSync(catalogPath)),
      public_catalog_sha256: hash(packagedCatalogBytes),
      package_manifest_sha256: hash(readFileSync(join(packageRoot, 'package-manifest.json'))),
      archive: { path: 'site.zip', bytes: archiveBytes.length, sha256: hash(archiveBytes) },
      reports: packagedCatalog.reports.map((entry) => ({ id: entry.id, sha256: entry.sha256 })),
      files: relativeFiles(staging).map((path) => {
        const bytes = readFileSync(join(staging, ...path.split('/')));
        return { path, bytes: bytes.length, sha256: hash(bytes) };
      }),
    };
    writeFileSync(join(staging, 'deploy-manifest.json'), json(manifest), 'utf8');
    const verification = verifyPublicRelease(staging);
    if (verification.length) throw new Error(`Public release verification failed: ${verification.join('; ')}`);
    replaceDirectory(staging, outputRoot, options.force ?? false);
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
  console.error('This legacy viewer-side projection CLI is disabled for deployment packaging. Use stage:public-reports with a framework-produced Phase 5A postgame_public source, then build:public and verify:public-build.');
  process.exitCode = 2;
}
