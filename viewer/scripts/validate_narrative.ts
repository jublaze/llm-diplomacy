import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import {
  validateHistorianNarrative,
  validateHistorianNarrativeSchema,
  validateNarrativeOutline,
  validateNarrativePlan,
  validateNarrativePlanSchema,
  type NarrativeOutlineV1,
  type NarrativePlanV1,
} from '../src/narrative-model.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';

const option = (name: string) => {
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
};
const reportPath = option('--report');
const planPath = option('--plan');
const outlinePath = option('--outline');
const narrativePath = option('--narrative');
if (!reportPath || !planPath || !outlinePath || !narrativePath) {
  console.error('Usage: node --experimental-strip-types scripts/validate_narrative.ts --report <report.json> --plan <plan.json> --outline <outline.json> --narrative <historian.json>');
  process.exitCode = 2;
} else {
  try {
    const reportBytes = readFileSync(reportPath);
    const report = JSON.parse(reportBytes.toString('utf8'));
    const plan = JSON.parse(readFileSync(planPath, 'utf8')) as NarrativePlanV1;
    const outline = JSON.parse(readFileSync(outlinePath, 'utf8')) as NarrativeOutlineV1;
    const narrative = JSON.parse(readFileSync(narrativePath, 'utf8'));
    const reportSha256 = createHash('sha256').update(reportBytes).digest('hex');
    const errors = [
      ...validateSchema(report), ...validateBundle(report),
      ...validateNarrativePlanSchema(plan), ...validateNarrativePlan(plan, report, { reportSha256 }),
      ...validateNarrativeOutline(outline, plan),
      ...validateHistorianNarrativeSchema(narrative), ...validateHistorianNarrative(narrative, report, { reportSha256, audience: plan.audience, plan }),
    ];
    if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1; }
    else console.log(`${narrativePath}: narrative package valid for ${reportPath}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
