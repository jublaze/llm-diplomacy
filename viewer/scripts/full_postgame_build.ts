import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildReportCatalog } from './build_report_catalog.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import { isStaticPublicBundle, resolveBundleAudience } from '../src/report-model/audience.ts';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const publication = resolve(root, '../publication');
const lock = JSON.parse(readFileSync(join(publication, 'luna-release-lock.json'), 'utf8'));
const sha = (value: Buffer) => createHash('sha256').update(value).digest('hex');
const files = (directory: string): string[] => readdirSync(directory, {withFileTypes: true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(x => {
  if (x.isSymbolicLink()) throw new Error('Linked build input refused');
  return x.isDirectory() ? files(join(directory,x.name)) : [join(directory,x.name)];
});
const manifest = (directory: string) => files(directory).map(path => ({path: relative(directory,path).replaceAll('\\','/'), sha256:sha(readFileSync(path)), bytes:statSync(path).size}));

export function verifyReport(path: string) {
  const bytes=readFileSync(path); if(sha(bytes)!==lock.report_sha256) throw new Error('Report is not release-locked');
  const report=JSON.parse(bytes.toString('utf8')); const errors=[...validateSchema(report),...validateBundle(report)];
  if(errors.length) throw new Error(errors.join('\n'));
  if(resolveBundleAudience(report).mode!=='full_postgame_public') throw new Error('Full release policy required');
  if(isStaticPublicBundle(report)) throw new Error('Full release must remain outside the old public-only policy');
  return report;
}
export function stage(data: string, output: string) {
  if(existsSync(output)) throw new Error('Stage destination exists');
  execFileSync(process.env.PYTHON ?? 'python',[join(publication,'full_postgame.py'),'verify','--data-root',resolve(data),'--lock',join(publication,'luna-release-lock.json')],{stdio:'pipe'});
  verifyReport(join(data,'report.json')); mkdirSync(output,{recursive:true});
  buildReportCatalog({sourceRoot:resolve(data),outputRoot:join(output,'reports')});
  cpSync(join(root,'public/maps'),join(output,'maps'),{recursive:true});
  for(const name of ['credits.html','THIRD_PARTY_NOTICES.json']) cpSync(join(publication,name),join(output,name));
  cpSync(join(data,'release-receipt.json'),join(output,'release-receipt.json'));
  const audit={schema_version:'diplomacy_full_postgame_stage_v1',policy_version:lock.policy_version,report_sha256:lock.report_sha256,files:manifest(output)};
  writeFileSync(join(output,'full-postgame-stage.json'),JSON.stringify(audit,null,2)+'\n');
  return audit;
}
export function verifyOutput(output: string, built=false) {
  const audit=JSON.parse(readFileSync(join(output,'full-postgame-stage.json'),'utf8'));
  if(audit.policy_version!==lock.policy_version||audit.report_sha256!==lock.report_sha256) throw new Error('Wrong stage policy');
  verifyReport(join(output,'reports/bundles/game_001/report.json'));
  for(const file of audit.files) {
    if(!/^[\w./-]+$/.test(file.path)||file.path.split('/').includes('..'))throw new Error('Unsafe audit path');
    if(sha(readFileSync(join(output,file.path)))!==file.sha256)throw new Error('Changed stage file '+file.path);
  }
  const allowed=new Set([...audit.files.map((x:any)=>x.path),'full-postgame-stage.json']);
  for(const path of files(output)) {
    const name=relative(output,path).replaceAll('\\','/');
    if(!allowed.has(name) && !(built && (name==='index.html'||name==='.nojekyll'||/^assets\/[\w.-]+\.(js|css)$/.test(name)))) throw new Error('Unexpected deployment file '+name);
  }
  if(built) {
    const html=readFileSync(join(output,'index.html'),'utf8');
    if(!html.includes('assets/'))throw new Error('Built entry point missing');
    const scripts=files(join(output,'assets')).filter(p=>p.endsWith('.js')).map(p=>readFileSync(p,'utf8')).join('');
    if(!scripts.includes('Full postgame research release')||!scripts.includes('credits.html'))throw new Error('Disclosure or visible credits missing');
  }
  return {policy:lock.policy_version,files:files(output).length,bytes:files(output).reduce((n,p)=>n+statSync(p).size,0),errors:0};
}
if(process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const [action,first,second]=process.argv.slice(2);
  console.log(JSON.stringify(action==='stage'?stage(first,second):verifyOutput(first,action==='verify-build')));
}
