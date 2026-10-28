import { createHash } from 'node:crypto';
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  createNarrativeOutline,
  createNarrativePlan,
  createSavedProviderRequest,
  createTemplateNarrative,
  type NarrativeProviderRequestV1,
} from '../src/narrative-generator.ts';
import {
  validateHistorianNarrative,
  validateHistorianNarrativeSchema,
  validateNarrativeOutline,
  validateNarrativePlan,
  validateNarrativePlanSchema,
  type HistorianNarrativeV1,
  type NarrativeAudience,
} from '../src/narrative-model.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import type { Report } from '../src/viewer-model.ts';

export type BuildNarrativePackageOptions = {
  reportPath: string;
  outputRoot: string;
  audience: NarrativeAudience;
  force?: boolean;
};

const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
const hash = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
const inside = (parent: string, child: string) => {
  const path = relative(resolve(parent), resolve(child));
  return path === '' || (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path));
};

function readReport(reportPath: string): { bytes: Buffer; report: Report; sha256: string } {
  const bytes = readFileSync(reportPath);
  let report: Report;
  try { report = JSON.parse(bytes.toString('utf8')) as Report; }
  catch (error) { throw new Error(`Factual report is not valid JSON: ${reportPath}`, { cause: error }); }
  const schemaErrors = validateSchema(report);
  if (schemaErrors.length) throw new Error(`Factual report schema validation failed: ${schemaErrors.join('; ')}`);
  const semanticErrors = validateBundle(report);
  if (semanticErrors.length) throw new Error(`Factual report semantic validation failed: ${semanticErrors.join('; ')}`);
  return { bytes, report, sha256: hash(bytes) };
}

function replaceDirectory(staging: string, destination: string, force: boolean) {
  if (existsSync(destination)) {
    if (!force) throw new Error(`Narrative destination already exists; pass --force to replace it: ${destination}`);
    rmSync(destination, { recursive: true, force: true });
  }
  try { renameSync(staging, destination); }
  catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'EPERM') throw error;
    cpSync(staging, destination, { recursive: true });
    rmSync(staging, { recursive: true, force: true });
  }
}

export function buildNarrativePackage(options: BuildNarrativePackageOptions) {
  const reportPath = resolve(options.reportPath);
  const outputRoot = resolve(options.outputRoot);
  if (inside(outputRoot, reportPath)) throw new Error('Narrative output root must not contain the factual report.');
  const factual = readReport(reportPath);
  const plan = createNarrativePlan(factual.report, factual.sha256, options.audience);
  const outline = createNarrativeOutline(plan);
  const template = createTemplateNarrative(plan, outline);
  const request = createSavedProviderRequest(plan, outline);
  const errors = [
    ...validateNarrativePlanSchema(plan),
    ...validateNarrativePlan(plan, factual.report, { reportSha256: factual.sha256 }),
    ...validateNarrativeOutline(outline, plan),
    ...validateHistorianNarrativeSchema(template),
    ...validateHistorianNarrative(template, factual.report, { reportSha256: factual.sha256, audience: options.audience, plan }),
  ];
  if (errors.length) throw new Error(`Generated narrative validation failed: ${errors.join('; ')}`);

  const gameId = factual.report.source.game_id as string;
  mkdirSync(outputRoot, { recursive: true });
  const staging = mkdtempSync(join(outputRoot, `.${gameId}-`));
  const destination = join(outputRoot, gameId);
  try {
    writeFileSync(join(staging, 'plan.json'), json(plan), 'utf8');
    writeFileSync(join(staging, 'outline.json'), json(outline), 'utf8');
    writeFileSync(join(staging, 'template-candidate.json'), json(template), 'utf8');
    writeFileSync(join(staging, 'request.json'), json(request), 'utf8');
    if (hash(readFileSync(reportPath)) !== factual.sha256) throw new Error('Factual report changed during narrative generation.');
    replaceDirectory(staging, destination, options.force ?? false);
  } catch (error) {
    if (existsSync(staging)) rmSync(staging, { recursive: true, force: true });
    throw error;
  }
  return { plan, outline, template, request, destination };
}

export type ProcessFixtureProviderOptions = {
  reportPath: string;
  outputRoot: string;
  request: NarrativeProviderRequestV1;
  rawResponse: unknown;
  runId: string;
};

export function processFixtureProviderResponse(options: ProcessFixtureProviderOptions): HistorianNarrativeV1 {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(options.runId)) throw new Error('Provider runId is unsafe.');
  const reportPath = resolve(options.reportPath);
  const outputRoot = resolve(options.outputRoot);
  if (inside(outputRoot, reportPath)) throw new Error('Narrative output root must not contain the factual report.');
  const factual = readReport(reportPath);
  const responseText = typeof options.rawResponse === 'string' ? options.rawResponse : json(options.rawResponse);
  let candidate: unknown = options.rawResponse;
  if (typeof candidate === 'string') {
    try { candidate = JSON.parse(candidate); }
    catch { candidate = null; }
  }
  const requestErrors: string[] = [];
  if (options.request.report_bundle_id !== factual.report.bundle_id) requestErrors.push('request bundle does not match factual report');
  if (options.request.report_sha256 !== factual.sha256) requestErrors.push('request report checksum mismatch');
  const errors = [
    ...requestErrors,
    ...validateHistorianNarrativeSchema(candidate),
    ...(candidate ? validateHistorianNarrative(candidate, factual.report, {
      reportSha256: factual.sha256,
      audience: options.request.audience,
      plan: options.request.plan,
    }) : []),
  ];
  if (errors.length) {
    const quarantine = join(outputRoot, 'quarantine', options.runId);
    mkdirSync(quarantine, { recursive: true });
    writeFileSync(join(quarantine, 'raw-response.json'), responseText, 'utf8');
    writeFileSync(join(quarantine, 'validation-errors.json'), json({ errors }), 'utf8');
    throw new Error(`Provider response validation failed; raw response quarantined at ${quarantine}: ${errors.join('; ')}`);
  }
  const gameId = factual.report.source.game_id as string;
  const destination = join(outputRoot, gameId, 'provider-candidate.json');
  mkdirSync(dirname(destination), { recursive: true });
  const staging = `${destination}.${options.runId}.tmp`;
  writeFileSync(staging, json(candidate), 'utf8');
  if (existsSync(destination)) rmSync(destination, { force: true });
  renameSync(staging, destination);
  if (hash(readFileSync(reportPath)) !== factual.sha256) throw new Error('Factual report changed during provider response processing.');
  return candidate as HistorianNarrativeV1;
}

function option(name: string): string | undefined {
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const reportPath = option('--report');
  const outputRoot = option('--output-root');
  const audience = option('--audience') as NarrativeAudience | undefined;
  if (!reportPath || !outputRoot || !['public', 'omniscient_postgame'].includes(audience ?? '')) {
    console.error('Usage: node --experimental-strip-types scripts/build_narratives.ts --report <report.json> --output-root <narratives> --audience <public|omniscient_postgame> [--force]');
    process.exitCode = 2;
  } else {
    try {
      const built = buildNarrativePackage({ reportPath, outputRoot, audience: audience!, force: process.argv.includes('--force') });
      console.log(`Built review-pending narrative package at ${built.destination}.`);
    } catch (error) {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    }
  }
}
