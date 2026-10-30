import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildReportCatalog } from './build_report_catalog.ts';
import { verifyReport as verifyLuna } from './full_postgame_build.ts';
import { validateSchema } from '../src/report-model/validate-schema.ts';
import { validateBundle } from '../src/report-model/validate.ts';
import { resolveBundleAudience } from '../src/report-model/audience.ts';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const publication = resolve(root, '../publication');
const terraLock = JSON.parse(readFileSync(join(publication, 'terra-release-lock.json'), 'utf8'));
const successorLock = JSON.parse(readFileSync(join(publication, 'luna-successor-release-lock.json'), 'utf8'));
const lunaLock = JSON.parse(readFileSync(join(publication, 'luna-release-lock.json'), 'utf8'));
const sha = (b: Buffer) => createHash('sha256').update(b).digest('hex');
const files = (d: string): string[] => readdirSync(d, { withFileTypes: true }).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(x=>{
  if(x.isSymbolicLink()) throw new Error('Linked input refused');
  return x.isDirectory() ? files(join(d,x.name)) : [join(d,x.name)];
});
const manifest = (d: string) => files(d).map(p=>({path:relative(d,p).replaceAll('\\','/'),sha256:sha(readFileSync(p)),bytes:statSync(p).size}));
const ids = ['game_001','terra61-1028-g1--game_001','terra61-1028-g2--game_001','luna1912-memory-1028-plan--game_001'];

function verifyTerra(game: 'g1' | 'g2', dir: string) {
  execFileSync(process.env.PYTHON ?? 'python',[join(publication,'terra_postgame.py'),'verify','--game',game,'--data-root',resolve(dir),'--lock',join(publication,'terra-release-lock.json')],{stdio:'pipe'});
  const report=JSON.parse(readFileSync(join(dir,'report.json'),'utf8'));
  const errors=[...validateSchema(report),...validateBundle(report)];
  if(errors.length || resolveBundleAudience(report).mode!=='full_postgame_public') throw new Error(`Terra ${game} validation failed: ${errors.join('; ')}`);
}

export function stage(luna: string, terra: string, successor: string, output: string) {
  const destination = resolve(output);
  for (const input of [luna, terra, successor]) {
    const source = resolve(input);
    const fromSource = relative(source, destination);
    const fromDestination = relative(destination, source);
    const inside = (p: string) => p === '' || (p !== '..' && !p.startsWith(`..${sep}`) && !isAbsolute(p));
    if (inside(fromSource) || inside(fromDestination)) throw new Error('Stage destination overlaps input');
  }
  if(existsSync(output)) throw new Error('Stage destination exists');
  execFileSync(process.env.PYTHON ?? 'python',[join(publication,'full_postgame.py'),'verify','--data-root',resolve(luna),'--lock',join(publication,'luna-release-lock.json')],{stdio:'pipe'});
  verifyLuna(join(luna,'report.json'));
  for(const g of ['g1','g2'] as const) verifyTerra(g,join(terra,g));
  execFileSync(process.env.PYTHON ?? 'python',[join(publication,'luna_successor_postgame.py'),'verify','--game','g2','--data-root',resolve(successor,'g2'),'--lock',join(publication,'luna-successor-release-lock.json')],{stdio:'pipe'});
  mkdirSync(output,{recursive:true});
  const source=join(output,'.sources');mkdirSync(source);
  for(const [name,dir] of [['luna',luna],['g1',join(terra,'g1')],['g2',join(terra,'g2')],['successor',join(successor,'g2')]]) {
    mkdirSync(join(source,name));cpSync(join(dir,'report.json'),join(source,name,'report.json'));
  }
  buildReportCatalog({sourceRoot:source,outputRoot:join(output,'reports'),experimentCatalogIds:true});
  rmSync(source,{recursive:true});
  cpSync(join(root,'public/maps'),join(output,'maps'),{recursive:true});
  for(const name of ['credits.html','THIRD_PARTY_NOTICES.json']) cpSync(join(publication,name),join(output,name));
  mkdirSync(join(output,'release-receipts'));
  for(const [name,dir] of [['luna',luna],['g1',join(terra,'g1')],['g2',join(terra,'g2')],['successor',join(successor,'g2')]]) cpSync(join(dir,'release-receipt.json'),join(output,'release-receipts',`${name}.json`));
  const audit={schema_version:'diplomacy_postgame_collection_stage_v1',terra_asset_sha256:terraLock.asset_sha256,files:manifest(output)};
  writeFileSync(join(output,'collection-stage.json'),JSON.stringify(audit,null,2)+'\n');return audit;
}

export function verifyOutput(output: string, built=false) {
  const audit=JSON.parse(readFileSync(join(output,'collection-stage.json'),'utf8'));
  if(audit.schema_version!=='diplomacy_postgame_collection_stage_v1'||audit.terra_asset_sha256!==terraLock.asset_sha256) throw new Error('Wrong collection stage');
  const catalog=JSON.parse(readFileSync(join(output,'reports/catalog.json'),'utf8'));
  if(JSON.stringify(catalog.reports.map((x:any)=>x.id).sort())!==JSON.stringify([...ids].sort())) throw new Error('Wrong collection catalog');
  verifyLuna(join(output,'reports/bundles/game_001/report.json'));
  for(const [g,id] of [['g1',ids[1]],['g2',ids[2]]]) {
    const file=join(output,'reports/bundles',id,'report.json');
    if(sha(readFileSync(file))!==terraLock.games[g].report_sha256) throw new Error('Terra report changed');
    const report=JSON.parse(readFileSync(file,'utf8'));const errors=[...validateSchema(report),...validateBundle(report)];
    if(errors.length||resolveBundleAudience(report).mode!=='full_postgame_public') throw new Error('Terra report invalid');
  }
  const successorFile=join(output,'reports/bundles',ids[3],'report.json');
  if(sha(readFileSync(successorFile))!==successorLock.games.g2.report_sha256) throw new Error('Luna successor report changed');
  const successorReport=JSON.parse(readFileSync(successorFile,'utf8'));
  if([...validateSchema(successorReport),...validateBundle(successorReport)].length || resolveBundleAudience(successorReport).mode!=='full_postgame_public') throw new Error('Luna successor report invalid');
  for(const f of audit.files) {
    if(!/^[\w./-]+$/.test(f.path)||f.path.split('/').includes('..')||sha(readFileSync(join(output,f.path)))!==f.sha256) throw new Error('Changed or unsafe stage file');
  }
  const allowed=new Set([...audit.files.map((x:any)=>x.path),'collection-stage.json']);
  const expected = new Set(['credits.html','maps/standard_clean.LICENSE.md','maps/standard_clean.svg','THIRD_PARTY_NOTICES.json',
    'release-receipts/luna.json','release-receipts/g1.json','release-receipts/g2.json','release-receipts/successor.json','reports/catalog.json',
    ...ids.map(id=>`reports/bundles/${id}/report.json`),'collection-stage.json']);
  if(allowed.size!==expected.size || [...allowed].some(name=>!expected.has(name)) || audit.files.length!==expected.size-1) throw new Error('Unexpected stage manifest');
  for(const [g,checksum] of [['luna',lunaLock.receipt_sha256],['g1',terraLock.games.g1.receipt_sha256],['g2',terraLock.games.g2.receipt_sha256],['successor',successorLock.games.g2.receipt_sha256]])
    if(sha(readFileSync(join(output,'release-receipts',`${g}.json`)))!==checksum) throw new Error('Receipt changed');
  for(const p of files(output)) {const n=relative(output,p).replaceAll('\\','/');if(!allowed.has(n)&&!(built&&(n==='index.html'||n==='.nojekyll'||/^assets\/[\w.-]+\.(js|css)$/.test(n))))throw new Error(`Unexpected deployment file ${n}`);}
  if(built) {
    if(!readFileSync(join(output,'index.html'),'utf8').includes('assets/'))throw new Error('Built entry point missing');
    const scripts=files(join(output,'assets')).filter(p=>p.endsWith('.js')).map(p=>readFileSync(p,'utf8')).join('');
    if(!scripts.includes('Full postgame research release')||!scripts.includes('credits.html'))throw new Error('Disclosure or visible credits missing');
  }
  return {reports:ids.length,files:files(output).length,errors:0};
}
if(process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const [action,...args]=process.argv.slice(2);
  console.log(JSON.stringify(action==='stage'?stage(args[0],args[1],args[2],args[3]):verifyOutput(args[0],action==='verify-build')));
}
