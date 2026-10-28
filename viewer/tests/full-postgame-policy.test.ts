import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { resolveBundleAudience, isStaticPublicBundle } from '../src/report-model/audience.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import { verifyReport } from '../scripts/full_postgame_build.ts';

describe('separate full-postgame release policy',()=>{
  it('does not accept blind approval flags or use the existing public-only policy',()=>{
    const fake={extensions:{audience:{schema_version:'diplomacy_report_audience_v1',policy_version:'diplomacy_full_postgame_release_v1',mode:'full_postgame_public',approved_for_static_public_distribution:true}}};
    expect(()=>resolveBundleAudience(fake)).toThrow(); expect(isStaticPublicBundle(fake)).toBe(false);
  });
  it('retains a reachable map credit and release disclosure in the source',()=>{
    const credits=readFileSync(resolve('../publication/credits.html'),'utf8');
    for(const text of ['Martin Asal','Diplomacy.svg','CC BY-SA 3.0','Modifications','Raw API transcripts'])expect(credits).toContain(text);
    expect(readFileSync('src/main.tsx','utf8')).toContain('credits.html');
  });
  it.skipIf(!process.env.LUNA_RELEASE_DATA)('loads the locked real dataset and rejects lost memory',()=>{
    const report=verifyReport(resolve(process.env.LUNA_RELEASE_DATA!,'report.json'));
    expect(validateSchema(report)).toEqual([]);expect(validateBundle(report)).toEqual([]);
    expect(Object.keys(report.messages)).toHaveLength(1503);expect(Object.keys(report.private_records)).toHaveLength(357);
    expect(resolveBundleAudience(report).mode).toBe('full_postgame_public');expect(isStaticPublicBundle(report)).toBe(false);
    const changed=structuredClone(report); changed.extensions.audience.projection.source_bundle_sha256='0'.repeat(64);
    expect(validateBundle(changed).length).toBeGreaterThan(0);
  });
});
