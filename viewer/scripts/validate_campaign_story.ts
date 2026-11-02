import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { validateCampaignStory } from '../src/campaign-story.ts';

const [storyPath, catalogPath] = process.argv.slice(2);
if (!storyPath || !catalogPath) throw new Error('Usage: validate_campaign_story.ts <story.json> <catalog.json>');
const bytes = readFileSync(storyPath);
const catalog = JSON.parse(readFileSync(catalogPath, 'utf8'));
if (createHash('sha256').update(bytes).digest('hex') !== catalog.campaign_story?.sha256) throw new Error('Story/catalog byte checksum mismatch.');
const story = validateCampaignStory(JSON.parse(bytes.toString('utf8')), catalog);
console.log(JSON.stringify({ games: story.games.length, episodes: story.episodes.length, portraits: story.portraits?.length ?? 0, records: Object.keys(story.records).length }));
