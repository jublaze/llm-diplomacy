import { useEffect, useRef } from 'react';
import type { CampaignStory, StoryRecord, StoryBlock } from '../campaign-story';
import type { EvidenceTarget } from '../viewer-model';
import './campaign-reader.css';

type Explore = { game: string; phase?: string; target?: EvidenceTarget | null; power?: string };
const playerName = (id: string) => id.split('_').map(part=>part==='ii'?'II':part.charAt(0).toUpperCase()+part.slice(1)).join(' ');
export function CampaignReader({ story, onExplore, onLibrary }: { story: CampaignStory; onExplore(value: Explore): void; onLibrary(): void }) {
  const params = new URLSearchParams(location.hash.slice(1));
  const chapter = params.get('chapter'); const player = params.get('player');
  const selected = story.episodes.find(e => e.id === chapter) ?? story.episodes[0];
  const ids = (params.get('record') ?? '').split(',').filter(id => story.records[id]);
  const originals = ids.flatMap(id => story.records[id].parts ?? [id]);
  const records = [...new Set(originals)].map(id => story.records[id]);
  const dialog = useRef<HTMLDialogElement>(null); const trigger = useRef<HTMLElement | null>(null);
  const navigate = (values: Record<string,string | null>) => { const next = new URLSearchParams(location.hash.slice(1)); next.set('view','story'); for (const [key,value] of Object.entries(values)) { if (value) next.set(key,value); else next.delete(key); } location.hash = next.toString(); };
  const close = () => { navigate({ record:null }); requestAnimationFrame(() => trigger.current?.focus()); };
  useEffect(() => { if (records.length) { if (!dialog.current?.open) dialog.current?.showModal(); if (dialog.current) dialog.current.scrollTop=0; } else dialog.current?.close(); }, [params.get('record')]);
  useEffect(() => { if (chapter || player) document.getElementById('story-title')?.scrollIntoView({block:'start'}); }, [chapter,player]);
  const sources = (refs: string[]) => <button className="story-citation" onClick={e => { trigger.current=e.currentTarget; navigate({record:refs.join(',')}); }}>Read sources{refs.length>1?` · ${refs.length}`:''}</button>;
  const choose = (id: string) => navigate({chapter:id,player:null,record:null});
  const game = story.games.find(g => g.id === selected.game)!;
  const index = story.episodes.indexOf(selected);
  const seriesGames = story.games.filter(g => g.series === game.series);
  const playerGames = seriesGames.filter(g => !player || Boolean(g.assignments[player]));
  const playerIds = [...new Set(seriesGames.flatMap(g => Object.keys(g.assignments)))];
  const memoryNote = story.provenance.memory_notes?.[game.series ?? 'Campaign'] ?? story.provenance.memory_note;
  const portrait = story.portraits?.find(p=>p.series===game.series&&p.player===player);
  const prose = (blocks: StoryBlock[]) => <article className="story-prose">{blocks.map((block,i)=>block.kind==='quote'?<blockquote key={i}><p>{block.text}</p>{sources(block.refs)}</blockquote>:<p key={i}>{block.text} {sources(block.refs)}</p>)}</article>;
  const download = (record: StoryRecord) => { const bytes=Uint8Array.from(atob(record.bytes ?? ''), c=>c.charCodeAt(0)); const url=URL.createObjectURL(new Blob([bytes])); const a=document.createElement('a'); a.href=url;a.download=record.filename ?? 'original.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); };
  return <div className="campaign-reader">
    <header className="story-topbar"><a href="#view=story" className="story-brand">DIPLOMACY / CAMPAIGNS</a><nav aria-label="Reading views"><a href="#view=story">Read the story</a><button onClick={()=>navigate({player:playerIds[0],record:null})}>Follow a player</button><button onClick={onLibrary}>Explore the evidence</button></nav></header>
    <div className="story-layout"><aside className="story-contents"><p className="story-kicker">Campaigns</p><h2>{story.title}</h2>{story.games.map(g=><section key={g.id}><h3>{g.label}</h3>{story.episodes.filter(e=>e.game===g.id).map(e=><button aria-current={!player&&e.id===selected.id?'page':undefined} key={e.id} onClick={()=>choose(e.id)}><span>{e.years}</span>{e.title}</button>)}</section>)}<p className="story-kicker">Players · {game.series ?? 'Campaign'}</p><div className="story-player-links">{playerIds.map(a=><button key={a} onClick={()=>navigate({player:a,record:null})}>{playerName(a)}</button>)}</div></aside>
    <main className="story-main"><details className="story-mobile-nav"><summary>Choose a game, episode, or player</summary><label>Episode<select value={selected.id} onChange={e=>choose(e.target.value)}>{story.games.map(g=><optgroup key={g.id} label={g.label}>{story.episodes.filter(e=>e.game===g.id).map(e=><option key={e.id} value={e.id}>{e.years} · {e.title}</option>)}</optgroup>)}</select></label><label>Player<select value={player??''} onChange={e=>navigate({player:e.target.value,record:null})}><option value="">Read chronologically</option>{playerIds.map(a=><option key={a} value={a}>{playerName(a)}</option>)}</select></label></details>
      <p className="story-kicker">Source-backed campaign history · full postgame record</p>
      {player&&playerIds.includes(player)?<>
        <h1 id="story-title">Follow {playerName(player)}</h1><p className="story-deck">{playerGames.map(g=>`${g.label}: ${playerName(g.assignments[player])}`).join(' · ')}</p>
        {portrait&&<section className="story-portrait"><h2>{portrait.title}</h2>{prose(portrait.blocks)}</section>}
        <div className="story-assignments">{playerGames.map(g=><section key={g.id}><h2>{g.label} · {playerName(g.assignments[player])}</h2><button onClick={()=>onExplore({game:g.id,power:g.assignments[player]})}>Explore this power’s record</button><ol>{story.episodes.filter(e=>e.game===g.id&&e.players.includes(player)).map(e=><li key={e.id}><button onClick={()=>choose(e.id)}>{e.years} · {e.title}</button></li>)}</ol></section>)}</div>
        <h2>Identity and memory originals</h2><p>{memoryNote}</p><div className="story-record-shelf">{Object.entries(story.records).filter(([,r])=>seriesGames.some(g=>g.experiment_id===r.experiment_id)).filter(([,r])=>(r.bytes!==undefined&&('/'+r.artifact_path).includes(`/agents/${player}/`))||(['copied_memory','input_memory','persistent_memory','canonical_memory'].includes(r.kind)&&r.title.includes(` · ${player} · `))).map(([id,r])=><div key={id}>{sources([id])}<span>{r.title}</span></div>)}</div>
      </>:<>
        <p className="story-kicker">{game.label} · {selected.years} · Episode {index+1} of {story.episodes.length}</p><h1 id="story-title">{selected.title}</h1>
        {index===0&&<p className="story-deck">{story.subtitle} Read straight through, follow a player, or investigate the records behind each episode.</p>}
        {game.introduction&&story.episodes.find(e=>e.game===game.id)?.id===selected.id&&<section className="story-introduction" aria-label={`${game.label} introduction`}><h2>{game.introduction.title}</h2>{prose(game.introduction.blocks)}</section>}
        <div className="story-episode-tools"><button onClick={()=>onExplore({game:game.id,phase:story.records[selected.blocks.flatMap(b=>b.refs).find(id=>story.records[id].explorer)?.toString()??'']?.explorer?.phase})}>Explore {game.label} on the board</button><label>Follow a player<select value="" onChange={e=>navigate({player:e.target.value,record:null})}><option value="">Choose a player</option>{selected.players.map(a=><option key={a} value={a}>{playerName(a)}</option>)}</select></label></div>
        {prose(selected.blocks)}
        <nav className="story-next" aria-label="Episode navigation"><button disabled={index===0} onClick={()=>choose(story.episodes[index-1].id)}>← Previous episode</button><button disabled={index===story.episodes.length-1} onClick={()=>choose(story.episodes[index+1].id)}>Next episode →</button></nav>
        {story.episodes[index+1]&&story.episodes[index+1].game!==selected.game&&<aside className="story-memory"><h2>Between games: identity and memory</h2><p>{memoryNote}</p><p>Each player’s page includes its original identity and memory records.</p></aside>}
      </>}
      <footer className="story-footer">Explore the evidence to inspect boards, orders, threads, and reflections. This full postgame edition includes material private during play. <a href="credits.html">Map credits and licenses</a>.</footer>
    </main></div>
    <dialog ref={dialog} className="story-dialog" aria-labelledby="story-evidence-title" onCancel={e=>{e.preventDefault();close();}}><header><h2 id="story-evidence-title">Sources and original records</h2><button autoFocus aria-label="Close story evidence" onClick={close}>Close ×</button></header><div className="story-evidence-body">{records.map((record,i)=><section key={i}><p className="story-kicker">{record.kind} · {record.experiment_id}</p><h3>{record.title}</h3><p className="story-source-context">{record.context}</p><div className="story-extract">{record.text}</div>{record.explorer&&<button onClick={()=>{dialog.current?.close();onExplore(record.explorer!);}}>{record.explorer.target?'Open this record in the evidence explorer':'Explore the related phase'}</button>}<details><summary>Source extract and provenance</summary><p className="story-source-locator">{record.artifact_path}{record.pointer}<br/>SHA-256: {record.sha256}</p><pre>{record.raw}</pre></details>{record.bytes!==undefined&&<button onClick={()=>download(record)}>Download exact original</button>}</section>)}</div></dialog>
  </div>;
}
