import '@testing-library/jest-dom/vitest';
import { webcrypto, createHash } from 'node:crypto';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';
import { CampaignReader } from '../src/app/CampaignReader';
import { createInMemoryCatalog, validateCatalog } from '../src/report-loader';
import { loadCampaignStory, validateCampaignStory, type CampaignStory } from '../src/campaign-story';
import { syntheticReport } from './synthetic-report';

afterEach(()=>{cleanup();location.hash='';vi.unstubAllGlobals();});
function fixture() {
  const report=syntheticReport(1901,1901) as any;
  report.roster=[{agent_id:'first',power:'france',display_name:'Same profile',profile_version:'1'},{agent_id:'second',power:'germany',display_name:'Same profile',profile_version:'1'}];
  const data=createInMemoryCatalog({g:report});
  data.catalog.reports[0]={...data.catalog.reports[0],sha256:'a'.repeat(64),audience_mode:'omniscient_research',approved_for_static_public_distribution:false};
  const assignments=Object.fromEntries(report.roster.map((r:any)=>[r.agent_id,r.power]));
  const story:CampaignStory={schema_version:'campaign_story_v1',title:'Connected campaign',subtitle:'Bargains and their consequences.',audience:'omniscient_postgame',games:[{id:'g',label:'Game 1',experiment_id:report.source.experiment_id,report_sha256:'a'.repeat(64),assignments}],episodes:[{id:'opening',game:'g',years:'1901',title:'An opening bargain',players:Object.keys(assignments),blocks:[{kind:'paragraph',text:'The fleet entered the sea.',refs:['m']},{kind:'quote',text:'Stay out of the sea.',refs:['m']}]}],records:{m:{kind:'message',title:'An original message',text:'Stay out of the sea.',raw:'{"body":"Stay out of the sea."}',experiment_id:report.source.experiment_id,artifact_path:'messages.jsonl',pointer:'/line/1',sha256:'b'.repeat(64),explorer:{game:'g',phase:'1901_spring_movement',target:null}}},provenance:{reader_sha256:'c'.repeat(64),authored_source_sha256:'d'.repeat(64),memory_note:'Influence is unverified.'}};
  const text=JSON.stringify(story);
  data.catalog.campaign_story={story_url:'story.json',sha256:createHash('sha256').update(text).digest('hex'),audience:'omniscient_postgame'};
  return {data,story,text};
}

describe('native campaign reader',()=>{
  it('validates cited introductions and portraits with series-scoped identities',()=>{
    const {data,story}=fixture();story.games[0].series='Luna';
    const blocks=story.episodes[0].blocks;
    story.games[0].introduction={title:'Meet the players',blocks};
    story.portraits=Object.keys(story.games[0].assignments).map(player=>({series:'Luna',player,title:player,blocks}));
    story.editorial_complete=true;
    expect(validateCampaignStory(story,data.catalog)).toBe(story);
    for(const mutate of [(s:CampaignStory)=>{s.games[0].introduction!.blocks=[{kind:'paragraph',text:'Missing source',refs:['missing']}];},(s:CampaignStory)=>{s.portraits![0].series='Terra';},(s:CampaignStory)=>{s.portraits!.push(structuredClone(s.portraits![0]));},(s:CampaignStory)=>{s.portraits!.pop();},(s:CampaignStory)=>{delete s.games[0].introduction;}]){
      const bad=structuredClone(story);mutate(bad);expect(()=>validateCampaignStory(bad,data.catalog)).toThrow();
    }
  });
  it('renders a game introduction and the selected player trajectory',async()=>{
    vi.stubGlobal('crypto',webcrypto);HTMLElement.prototype.scrollIntoView=vi.fn();
    HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    const {data,story}=fixture();story.games[0].series='Luna';
    story.games[0].introduction={title:'The seven-way contest',blocks:[{kind:'paragraph',text:'Their starting positions shaped the bargain.',refs:['m']}]};
    story.portraits=[{series:'Luna',player:'first',title:'A changing partnership',blocks:[{kind:'paragraph',text:'First secured the fleet agreement, then changed partners.',refs:['m']}]}];
    const text=JSON.stringify(story);data.catalog.campaign_story!.sha256=createHash('sha256').update(text).digest('hex');
    render(<App initialData={data} fetcher={async()=>({ok:true,status:200,statusText:'OK',text:async()=>text})}/>);
    expect(await screen.findByRole('heading',{name:'The seven-way contest'})).toBeVisible();
    fireEvent.click(screen.getByRole('button',{name:'Follow a player'}));
    expect(await screen.findByRole('heading',{name:'A changing partnership'})).toBeVisible();
    expect(screen.getByText('First secured the fleet agreement, then changed partners.')).toBeVisible();
    expect(screen.queryByRole('heading',{name:'The seven-way contest'})).toBeNull();
  });
  it('rejects changed report bindings, merged identities, unresolved sources, and altered quotations',()=>{
    const {data,story}=fixture();
    expect(validateCampaignStory(story,data.catalog)).toBe(story);
    for(const mutate of [(s:CampaignStory)=>s.games[0].report_sha256='f'.repeat(64),(s:CampaignStory)=>s.episodes[0].players.push('unknown'),(s:CampaignStory)=>s.episodes[0].players.pop(),(s:CampaignStory)=>{s.records.m.artifact_path='games/wrong/messages.jsonl';},(s:CampaignStory)=>s.episodes[0].blocks[0].refs.push('missing'),(s:CampaignStory)=>s.episodes[0].blocks[1].text='An invented quote']){
      const bad=structuredClone(story);mutate(bad);expect(()=>validateCampaignStory(bad,data.catalog)).toThrow();
    }
  });
  it('blocks private story delivery for public catalogs before fetching and rejects unsafe story URLs',async()=>{
    const {data}=fixture();const fetcher=vi.fn();
    const publicCatalog=structuredClone(data.catalog);publicCatalog.reports[0].audience='public';
    await expect(loadCampaignStory(publicCatalog,'https://example.test/catalog.json',fetcher)).rejects.toThrow('research catalog');
    expect(fetcher).not.toHaveBeenCalled();
    const invalid=structuredClone(data.catalog);invalid.campaign_story!.story_url='../private.json';
    expect(()=>validateCatalog(invalid)).toThrow('story reference');
    expect(()=>validateCatalog(publicCatalog)).toThrow('research catalog');
  });
  it('accepts only explicit reviewed full-postgame stories and isolates same-named players by series',async()=>{
    const {data,story}=fixture();
    data.catalog.reports[0].audience_mode='full_postgame_public';data.catalog.reports[0].approved_for_static_public_distribution=true;
    expect(()=>validateCampaignStory(story,data.catalog)).toThrow();
    story.publication_policy='diplomacy_campaign_story_release_v1';data.catalog.campaign_story!.publication_policy=story.publication_policy;
    expect(validateCampaignStory(story,data.catalog)).toBe(story);
    HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    story.games[0].series='Luna';
    const other={...story.games[0],id:'terra',series:'Terra',label:'Terra game',experiment_id:'terra'};
    story.games.push(other);
    data.catalog.reports.push({...data.catalog.reports[0],id:'terra',experiment_id:'terra'});
    story.episodes.push({...story.episodes[0],id:'terra-opening',game:'terra',title:'Terra opening'});
    vi.stubGlobal('crypto',webcrypto);HTMLElement.prototype.scrollIntoView=vi.fn();
    const text=JSON.stringify(story);data.catalog.campaign_story!.sha256=createHash('sha256').update(text).digest('hex');
    location.hash='view=story&chapter=opening&player=first';
    render(<App initialData={data} fetcher={async()=>({ok:true,status:200,statusText:'OK',text:async()=>text})}/>);
    expect(await screen.findByRole('heading',{name:'Follow First'})).toBeVisible();
    expect(screen.getByText('Game 1: France')).toBeVisible();
    expect(screen.queryByRole('heading',{name:/Terra game/,level:2})).toBeNull();
  });
  it('verifies story bytes before accepting a changed narrative',async()=>{
    vi.stubGlobal('crypto',webcrypto);const {data,text}=fixture();
    await expect(loadCampaignStory(data.catalog,'https://example.test/catalog.json',async()=>({ok:true,status:200,statusText:'OK',text:async()=>text+' '}))).rejects.toThrow('checksum');
  });
  it('keeps player paths usable when the next game changes its roster',()=>{
    const {story}=fixture();story.games[0].series='Luna';
    story.games.push({...story.games[0],id:'later',label:'Later game',assignments:{second:'italy'}});
    story.episodes.push({...story.episodes[0],id:'later-opening',game:'later',players:['second']});
    HTMLElement.prototype.scrollIntoView=vi.fn();HTMLDialogElement.prototype.close=vi.fn();
    location.hash='view=story&chapter=opening&player=first';
    render(<CampaignReader story={story} onExplore={vi.fn()} onLibrary={vi.fn()}/>);
    expect(screen.getByRole('heading',{name:'Follow First'})).toBeVisible();
    expect(screen.getByText('Game 1: France')).toBeVisible();
    expect(screen.queryByRole('heading',{name:/Later game/,level:2})).toBeNull();
  });
  it('opens the story by default, shows source extracts before raw records, and returns from the existing explorer',async()=>{
    vi.stubGlobal('crypto',webcrypto);HTMLElement.prototype.scrollIntoView=vi.fn();
    HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    const {data,text}=fixture();
    render(<App initialData={data} fetcher={async()=>({ok:true,status:200,statusText:'OK',text:async()=>text})}/>);
    expect(await screen.findByRole('heading',{name:'An opening bargain'})).toBeVisible();
    expect(screen.queryByRole('heading',{name:'Between games: identity and memory'})).toBeNull();
    fireEvent.click(screen.getAllByRole('button',{name:'Read sources'})[0]);
    expect(await screen.findByRole('dialog')).toBeVisible();
    expect(screen.getByRole('heading',{name:'An original message'})).toBeVisible();
    expect(screen.getByText('Source extract and provenance')).toBeVisible();
    const evidence=screen.getByRole('dialog');evidence.scrollTop=500;
    fireEvent.click(screen.getByRole('button',{name:'Close story evidence'}));
    await waitFor(()=>expect(evidence).not.toHaveAttribute('open'));
    fireEvent.click(screen.getAllByRole('button',{name:'Read sources'})[0]);
    await waitFor(()=>expect(evidence).toHaveAttribute('open'));
    expect(evidence.scrollTop).toBe(0);
    fireEvent.click(screen.getByRole('button',{name:'Explore the related phase'}));
    expect(await screen.findByRole('heading',{name:'Spring 1901 scene'})).toBeVisible();
    fireEvent.click(screen.getByRole('button',{name:'← Return to the story'}));
    expect(await screen.findByRole('heading',{name:'An opening bargain'})).toBeVisible();
    expect(await screen.findByRole('dialog')).toBeVisible();
  });
});
