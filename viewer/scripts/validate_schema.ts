import { readFileSync } from 'node:fs';
import { validateSchema } from '../src/report-model/validate-schema.ts';

let failures = 0;
for (const file of process.argv.slice(2)) {
  try {
    const errors = validateSchema(JSON.parse(readFileSync(file, 'utf8')));
    if (errors.length) {
      failures++;
      console.error(`${file}\n  ${errors.join('\n  ')}`);
    } else console.log(`${file}: schema valid`);
  } catch (error) {
    failures++;
    console.error(`${file}: ${error instanceof Error ? error.message : error}`);
  }
}
process.exitCode = failures ? 1 : 0;
