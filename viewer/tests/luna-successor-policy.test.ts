import { describe, expect, it } from 'vitest';
import { resolveBundleAudience, isStaticPublicBundle } from '../src/report-model/audience.ts';

const bundle = () => ({
  bundle_id:'bundle', source:{experiment_id:'luna1912-memory-1028-plan'}, extensions:{audience:{
    schema_version:'diplomacy_report_audience_v1', policy_version:'diplomacy_luna_successor_postgame_release_v1',
    mode:'full_postgame_public', reader_description:'Released after play.', approved_for_static_public_distribution:true,
    projection:{generator:'publication/luna_successor_postgame.py',source_bundle_id:'bundle',source_bundle_sha256:'ed894823f8ca74b81586b494bdb805708082273de825eb2efd572e7280f6eab6'},
  }},
});
describe('Luna successor postgame policy',()=>{
  it('accepts the exact reviewed source without relaxing the old public-only policy',()=>{
    const report=bundle();expect(resolveBundleAudience(report).mode).toBe('full_postgame_public');expect(isStaticPublicBundle(report)).toBe(false);
  });
  it('rejects a substituted experiment, report hash, or bundle identity',()=>{
    for(const mutate of [(r:any)=>r.source.experiment_id='other',(r:any)=>r.extensions.audience.projection.source_bundle_sha256='0'.repeat(64),(r:any)=>r.bundle_id='other']) {
      const report=bundle();mutate(report);expect(()=>resolveBundleAudience(report)).toThrow('Invalid Luna successor');
    }
  });
});
