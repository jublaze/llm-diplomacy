import type { FetchLike, ReportCatalogV1 } from './report-loader';
import type { EvidenceTarget } from './viewer-model';

export type StoryRecord = { kind: string; title: string; parts?: string[]; text?: string; raw?: string; context?: string; experiment_id?: string; game_id?: string; artifact_path?: string; pointer?: string; sha256?: string; bytes?: string; filename?: string; explorer?: { game: string; phase: string; target: EvidenceTarget | null } };
export type StoryBlock = { kind: 'paragraph' | 'quote'; text: string; refs: string[] };
export type StoryEpisode = { id: string; game: string; years: string; title: string; players: string[]; blocks: StoryBlock[] };
export type StorySection = { title: string; blocks: StoryBlock[] };
export type StoryPortrait = StorySection & { series: string; player: string };
export type CampaignStory = { schema_version: 'campaign_story_v1'; publication_policy?: string; editorial_complete?: boolean; title: string; subtitle: string; audience: 'omniscient_postgame'; games: { id: string; label: string; experiment_id: string; source_game_id?: string; series?: string; report_sha256: string; assignments: Record<string,string>; introduction?: StorySection }[]; episodes: StoryEpisode[]; portraits?: StoryPortrait[]; records: Record<string,StoryRecord>; provenance: { reader_sha256: string; authored_source_sha256: string; memory_note: string; memory_notes?: Record<string,string> } };
export const CAMPAIGN_PUBLICATION_POLICY = 'diplomacy_campaign_story_release_v1';
export type CampaignStoryReference = { publication_policy?: string; story_url: string; sha256: string; audience: 'omniscient_postgame' };

export function validateCampaignStory(value: unknown, catalog: ReportCatalogV1): CampaignStory {
  const story = value as CampaignStory;
  if (!story || story.schema_version !== 'campaign_story_v1' || story.audience !== 'omniscient_postgame' || typeof story.title !== 'string' || typeof story.subtitle !== 'string' || !Array.isArray(story.games) || !story.games.length || !Array.isArray(story.episodes) || !story.episodes.length || !story.records || !story.provenance || typeof story.provenance.memory_note !== 'string') throw new Error('Invalid campaign story.');
  const ids = new Set<string>();
  for (const game of story.games) {
    const entry = catalog.reports.find(r => r.id === game.id);
    if (!entry || ids.has(game.id) || entry.sha256 !== game.report_sha256 || entry.experiment_id !== game.experiment_id || entry.audience !== 'omniscient_postgame' || !((entry.audience_mode === 'omniscient_research' && entry.approved_for_static_public_distribution === false) || (story.publication_policy === CAMPAIGN_PUBLICATION_POLICY && catalog.campaign_story?.publication_policy === CAMPAIGN_PUBLICATION_POLICY && entry.audience_mode === 'full_postgame_public' && entry.approved_for_static_public_distribution === true))) throw new Error('Story report binding or audience mismatch.');
    if (game.source_game_id && game.source_game_id !== (entry.source_game_id ?? entry.id)) throw new Error('Story game occurrence mismatch.');
    if (!game.assignments || !Object.keys(game.assignments).length || Object.values(game.assignments).some(p => typeof p !== 'string')) throw new Error('Missing story identities.');
    ids.add(game.id);
  }
  const episodes = new Set<string>();
  const checkBlocks = (blocks: StoryBlock[]) => {
    if (!Array.isArray(blocks) || !blocks.length) throw new Error('Missing authored passages.');
    for (const block of blocks) {
      if (!['paragraph','quote'].includes(block.kind) || typeof block.text !== 'string' || !Array.isArray(block.refs) || !block.refs.length || block.refs.some(id => !story.records[id])) throw new Error('Unresolved story citation.');
      if (block.kind === 'quote' && !block.refs.some(id => story.records[id].text?.includes(block.text))) throw new Error('Inexact story quotation.');
    }
  };
  for (const episode of story.episodes) {
    const game = story.games.find(g => g.id === episode.game);
    if (!game || !/^[a-zA-Z0-9_-]+$/.test(episode.id) || episodes.has(episode.id) || typeof episode.title !== 'string' || typeof episode.years !== 'string' || !Array.isArray(episode.players) || episode.players.some(p => !game.assignments[p]) || !Array.isArray(episode.blocks) || !episode.blocks.length) throw new Error('Invalid story episode or identity.');
    episodes.add(episode.id);
    checkBlocks(episode.blocks);
  }
  const portraits = new Set<string>();
  if (story.portraits !== undefined && !Array.isArray(story.portraits)) throw new Error('Invalid player portraits.');
  for (const portrait of story.portraits ?? []) {
    const key = JSON.stringify([portrait.series,portrait.player]);
    if (typeof portrait.title !== 'string' || typeof portrait.series !== 'string' || !portrait.series || typeof portrait.player !== 'string' || !story.games.some(g=>g.series===portrait.series&&g.assignments[portrait.player]) || portraits.has(key)) throw new Error('Invalid or duplicate series/player portrait.');
    portraits.add(key);checkBlocks(portrait.blocks);
  }
  for (const game of story.games) {
    if (game.introduction) {
      if (typeof game.introduction.title !== 'string') throw new Error('Invalid game introduction.');
      checkBlocks(game.introduction.blocks);
    }
    if (story.editorial_complete && (!game.introduction || Object.keys(game.assignments).some(p=>!portraits.has(JSON.stringify([game.series,p]))))) throw new Error('Incomplete game introductions or player portraits.');
    const covered = new Set(story.episodes.filter(e=>e.game===game.id).flatMap(e=>e.players));
    if (Object.keys(game.assignments).some(p=>!covered.has(p))) throw new Error('Incomplete game/player coverage.');
  }
  for (const record of Object.values(story.records)) {
    if (typeof record.title !== 'string') throw new Error('Invalid source title.');
    if (record.kind === 'group') {
      if (!Array.isArray(record.parts) || !record.parts.length || record.parts.some(id => !story.records[id] || story.records[id].kind === 'group')) throw new Error('Invalid evidence group.');
    } else {
      if (!story.games.some(g => g.experiment_id === record.experiment_id) || typeof record.text !== 'string' || typeof record.raw !== 'string' || typeof record.artifact_path !== 'string' || !/^[a-f0-9]{64}$/.test(record.sha256 ?? '')) throw new Error('Invalid story evidence provenance.');
      const sourceGame = record.game_id ?? /^games\/([^/]+)\//.exec(record.artifact_path)?.[1];
      if (record.explorer && !story.games.some(g => g.id === record.explorer?.game && g.experiment_id === record.experiment_id && (!sourceGame || (g.source_game_id ?? g.id) === sourceGame))) throw new Error('Cross-experiment explorer target.');
    }
  }
  return story;
}

export async function loadCampaignStory(catalog: ReportCatalogV1, catalogUrl: string, fetcher: FetchLike): Promise<CampaignStory> {
  const ref = catalog.campaign_story;
  if (!ref) throw new Error('No campaign story configured.');
  if (catalog.reports.some(entry => entry.audience !== 'omniscient_postgame' || !((entry.audience_mode === 'omniscient_research' && entry.approved_for_static_public_distribution === false) || (ref.publication_policy === CAMPAIGN_PUBLICATION_POLICY && entry.audience_mode === 'full_postgame_public' && entry.approved_for_static_public_distribution === true)))) throw new Error('Campaign stories require a reviewed full-postgame research catalog.');
  const response = await fetcher(new URL(ref.story_url, catalogUrl).href);
  if (!response.ok) throw new Error(`Story request failed: ${response.status}`);
  const text = await response.text();
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  const hash = Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2,'0')).join('');
  if (hash !== ref.sha256) throw new Error('Campaign story checksum mismatch.');
  return validateCampaignStory(JSON.parse(text),catalog);
}
