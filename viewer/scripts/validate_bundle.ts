import { readFileSync } from 'node:fs';
import { validateBundle } from '../src/report-model/validate.ts';

let failures = 0;
for (const file of process.argv.slice(2)) {
  try {
    const errors = validateBundle(JSON.parse(readFileSync(file, 'utf8')));
    if (errors.length) {
      failures++;
      console.error(`${file}\n${errors.map(error => `  ${error}`).join('\n')}`);
    } else console.log(`${file}: valid`);
  } catch (error) {
    failures++;
    console.error(`${file}: ${error instanceof Error ? error.message : error}`);
  }
}
process.exitCode = failures ? 1 : 0;
